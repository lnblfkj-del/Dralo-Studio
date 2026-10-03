"""Reuse media through new version rows without rewriting provider evidence."""

from copy import deepcopy

from sqlalchemy import select

from app.core.errors import AppError
from app.models import EpisodeProductionPlan, Job, SegmentScriptSnapshot, SegmentVideoVersion, VideoSegment, VideoSegmentShot
from app.services.asset_production_service import fingerprint
from app.services.production_snapshot_service import build_script_snapshot_content, generation_content

REUSE_KEY = "reuse_source_version_id"
EVIDENCE_KEY = "segment_video_candidate"
PLAN_METADATA = {"source", "parent_plan_id", "operation", "excluded_shot_ids", "confirmed_excluded_shot_ids",
                 "timeline_selected_lineage_key"}


def comparable_script(content):
    return {key: value for key, value in generation_content(content).items() if key not in {
        "segment_id", "plan_id", "order", "title", "parent_plan_id", "plan_source_type",
    }}


async def include_candidate_history(session, plan, segments, versions_by_segment):
    """Read old and late-arriving results without changing their frozen evidence."""
    by_lineage = {segment.lineage_key: segment for segment in segments}
    if not by_lineage:
        return
    rows = (await session.execute(
        select(SegmentVideoVersion, VideoSegment.lineage_key)
        .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
        .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
        .where(VideoSegment.episode_id == plan.episode_id,
               VideoSegment.lineage_key.in_(by_lineage),
               EpisodeProductionPlan.version < plan.version)
        .order_by(SegmentVideoVersion.id.desc())
    )).all()

    def origin(version):
        return (version.parameters or {}).get(REUSE_KEY) or version.id

    seen = {segment.id: {origin(v) for v in versions_by_segment[segment.id]} for segment in segments}
    for version, lineage in rows:
        segment = by_lineage[lineage]
        if origin(version) not in seen[segment.id]:
            versions_by_segment[segment.id].append(version)
            seen[segment.id].add(origin(version))
    for versions in versions_by_segment.values():
        versions.sort(key=origin)


async def reusable_inputs(session, segment, plan, version, current_content=None):
    """Compare against the original immutable snapshot, not a rewritten digest."""
    source_id = (version.parameters or {}).get(REUSE_KEY)
    source = await session.get(SegmentVideoVersion, source_id) if type(source_id) is int else version
    if source is None or (source.parameters or {}).get(REUSE_KEY):
        return False
    evidence = (source.parameters or {}).get(EVIDENCE_KEY) or {}
    if not isinstance(evidence, dict):
        return False
    if evidence != (version.parameters or {}).get(EVIDENCE_KEY):
        return False
    inputs = evidence.get("input_snapshot")
    if not isinstance(inputs, dict) or fingerprint(inputs) != evidence.get("input_fingerprint"):
        return False
    if inputs.get("continuity_dependency") or (segment.refs or {}).get("continuity"):
        return False
    original = await session.get(VideoSegment, source.segment_id)
    if original is None or original.episode_id != segment.episode_id or original.lineage_key != segment.lineage_key:
        return False
    if inputs.get("segment_id") != original.id or inputs.get("plan_id") != original.plan_id:
        return False
    if source.media_file_id != version.media_file_id or source.source_job_id != version.source_job_id:
        return False
    job = await session.get(Job, source.source_job_id) if source.source_job_id else None
    if job is None or job.status != "succeeded" or job.target_id != original.id:
        return False
    if (job.payload or {}).get("video_input_fingerprint") != evidence.get("input_fingerprint"):
        return False
    snapshot_id = evidence.get("script_snapshot_id")
    frozen = await session.get(SegmentScriptSnapshot, snapshot_id) if type(snapshot_id) is int else None
    if frozen is None or frozen.segment_id != original.id or frozen.fingerprint != evidence.get("script_fingerprint"):
        return False
    if fingerprint(frozen.content) != frozen.fingerprint or inputs.get("script_fingerprint") != frozen.fingerprint:
        return False
    old_plan = await session.get(EpisodeProductionPlan, original.plan_id)
    if old_plan is None or old_plan.provider_model_id != plan.provider_model_id or inputs.get("provider_model_id") != plan.provider_model_id:
        return False
    if old_plan.model_capability_snapshot != plan.model_capability_snapshot:
        return False
    if {k: v for k, v in (old_plan.parameters or {}).items() if k not in PLAN_METADATA} != {k: v for k, v in (plan.parameters or {}).items() if k not in PLAN_METADATA}:
        return False
    async def shot_signature(segment_id):
        return list((await session.execute(select(
            VideoSegmentShot.shot_id, VideoSegmentShot.order,
            VideoSegmentShot.start_time, VideoSegmentShot.end_time,
        ).where(VideoSegmentShot.segment_id == segment_id).order_by(VideoSegmentShot.order))).all())
    if await shot_signature(original.id) != await shot_signature(segment.id):
        return False
    try:
        current_content = current_content if current_content is not None else await build_script_snapshot_content(session, segment)
    except AppError:
        return False
    return comparable_script(frozen.content) == comparable_script(current_content)


async def carry_candidates(session, plan, segments):
    from app.services.segment_video_candidate_service import _media_state
    from app.models import MediaFile

    if not segments:
        return
    historical = (await session.execute(
        select(SegmentVideoVersion, VideoSegment.lineage_key)
        .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
        .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
        .where(VideoSegment.episode_id == plan.episode_id,
               VideoSegment.lineage_key.in_([segment.lineage_key for segment in segments]),
               EpisodeProductionPlan.version < plan.version)
        .order_by(EpisodeProductionPlan.version.desc(), SegmentVideoVersion.version)
    )).all()
    by_lineage = {}
    for version, lineage in historical:
        versions = by_lineage.setdefault(lineage, [])
        # Preserve the nearest plan's adoption choice, even if it is empty.
        if not versions or versions[0].segment_id == version.segment_id:
            versions.append(version)
    for segment in segments:
        versions = by_lineage.get(segment.lineage_key)
        if not versions:
            continue
        current = await build_script_snapshot_content(session, segment)
        for version in versions:
            media = await session.get(MediaFile, version.media_file_id)
            reusable = _media_state(media)[0] == "ready" and await reusable_inputs(session, segment, plan, version, current)
            session.add(SegmentVideoVersion(
                segment_id=segment.id, media_file_id=version.media_file_id,
                source_job_id=version.source_job_id, version=version.version,
                prompt=version.prompt, negative_prompt=version.negative_prompt,
                parameters={**deepcopy(version.parameters or {}), REUSE_KEY: (version.parameters or {}).get(REUSE_KEY, version.id)},
                is_final=version.is_final and reusable,
            ))
    await session.flush()
