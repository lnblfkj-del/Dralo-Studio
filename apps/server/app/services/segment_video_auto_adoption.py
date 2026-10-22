"""Adopt current, verified video results without letting late jobs replace newer work."""

from sqlalchemy import select, update

from app.models import EpisodeProduction, EpisodeProductionPlan, SegmentVideoVersion, VideoSegment
from app.services.segment_video_candidate_service import candidate_evidence


async def adopt_generated_video(session, job, result):
    version_id = result.get("segment_video_version_id")
    if job.job_type != "video" or job.target_type != "video_segment" or not version_id:
        return
    version = await session.get(SegmentVideoVersion, version_id)
    if version is None or version.source_job_id != job.id or version.segment_id != job.target_id:
        return
    segment = await session.get(VideoSegment, version.segment_id)
    if segment is None or segment.status == "archived":
        return
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == segment.episode_id))
    if production is None or production.active_plan_id != segment.plan_id:
        return
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    if plan is None:
        return
    evidence = await candidate_evidence(session, segment=segment, plan=plan, version=version)
    if not evidence["adoptable"]:
        job.result = {**result, "is_final": version.is_final, "auto_adoption_status": evidence["status"]}
        return
    newer = await session.scalar(select(SegmentVideoVersion.id).where(
        SegmentVideoVersion.segment_id == segment.id,
        SegmentVideoVersion.is_final.is_(True),
        SegmentVideoVersion.source_job_id > job.id,
    ).limit(1))
    if newer is not None:
        job.result = {**result, "is_final": False, "auto_adoption_status": "newer_result_adopted"}
        return
    await session.execute(update(SegmentVideoVersion).where(
        SegmentVideoVersion.segment_id == segment.id,
    ).values(is_final=False))
    version.is_final = True
    segment.status = "ready"
    job.result = {**result, "is_final": True, "auto_adoption_status": "adopted"}
    await session.flush()
