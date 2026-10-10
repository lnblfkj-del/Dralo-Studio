"""Serialization read model for episode production plans."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Asset,
    EpisodeProductionPlan,
    ProviderModel,
    Scene,
    SegmentVideoVersion,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.services import pricing_service
from app.services.production_snapshot_service import script_state


async def serialize_plan(session: AsyncSession, plan: EpisodeProductionPlan) -> dict[str, Any]:
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(VideoSegment.plan_id == plan.id)
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    segment_ids = [item.id for item in segments]
    unresolved_ids = {
        raw.get("asset_id")
        for segment in segments
        for raw in (segment.refs or {}).get("unresolved_assets", [])
        if isinstance(raw, dict) and type(raw.get("asset_id")) is int
    }
    optional_frame_ids: set[int] = set()
    if unresolved_ids:
        optional_frame_ids = {
            asset.id for asset in (await session.scalars(
                select(Asset).where(Asset.id.in_(unresolved_ids))
            )).all()
            if (asset.attributes or {}).get("derived_type") == "segment_first_frame"
        }
    links_by_segment: dict[int, list[VideoSegmentShot]] = {item.id: [] for item in segments}
    versions_by_segment: dict[int, list[SegmentVideoVersion]] = {item.id: [] for item in segments}
    if segment_ids:
        links = list(
            (
                await session.scalars(
                    select(VideoSegmentShot)
                    .where(VideoSegmentShot.segment_id.in_(segment_ids))
                    .order_by(VideoSegmentShot.segment_id, VideoSegmentShot.order)
                )
            ).all()
        )
        versions = list(
            (
                await session.scalars(
                    select(SegmentVideoVersion)
                    .where(SegmentVideoVersion.segment_id.in_(segment_ids))
                    .order_by(SegmentVideoVersion.segment_id, SegmentVideoVersion.version)
                )
            ).all()
        )
        for link in links:
            links_by_segment[link.segment_id].append(link)
        for version in versions:
            versions_by_segment[version.segment_id].append(version)
    from app.services.segment_video_candidate_service import candidate_evidence
    from app.services.segment_candidate_reuse_service import include_candidate_history

    await include_candidate_history(session, plan, segments, versions_by_segment)

    evidence_by_version: dict[int, dict[str, Any]] = {}
    for segment in segments:
        for version in versions_by_segment[segment.id]:
            evidence_by_version[version.id] = await candidate_evidence(
                session, segment=segment, plan=plan, version=version
            )
    shot_ids = [link.shot_id for values in links_by_segment.values() for link in values]
    shot_map = {
        item.id: item
        for item in (
            await session.scalars(select(Shot).where(Shot.id.in_(shot_ids)))
        ).all()
    } if shot_ids else {}
    scene_ids = {item.scene_id for item in shot_map.values()}
    scene_map = {
        item.id: item
        for item in (
            await session.scalars(select(Scene).where(Scene.id.in_(scene_ids)))
        ).all()
    } if scene_ids else {}
    provider_model = (
        await session.get(ProviderModel, plan.provider_model_id)
        if plan.provider_model_id is not None
        else None
    )
    return {
        "id": plan.id,
        "episode_id": plan.episode_id,
        "version": plan.version,
        "source_type": plan.source_type,
        "parent_plan_id": plan.parent_plan_id,
        "status": plan.status,
        "source_script_revision": plan.source_script_revision,
        "provider_model_id": plan.provider_model_id,
        "model_capability_snapshot": plan.model_capability_snapshot or {},
        "parameters": plan.parameters or {},
        "total_timeline_duration": plan.total_timeline_duration,
        "total_generation_duration": plan.total_generation_duration,
        "revision": plan.revision,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "segments": [
            {
                "id": segment.id,
                "lineage_key": segment.lineage_key,
                "parent_lineage_keys": segment.parent_lineage_keys or [],
                "order": segment.order,
                "title": segment.title,
                "generation_duration": segment.generation_duration,
                "timeline_duration": segment.timeline_duration,
                "trim_in": segment.trim_in,
                "trim_out": segment.trim_out,
                "prompt": segment.prompt,
                "negative_prompt": segment.negative_prompt,
                "parameters": segment.parameters or {},
                "refs": {
                    **(segment.refs or {}),
                    "unresolved_assets": [
                        raw for raw in (segment.refs or {}).get("unresolved_assets", [])
                        if not isinstance(raw, dict) or (
                            raw.get("role") not in {"first_frame", "last_frame"}
                            and raw.get("asset_id") not in optional_frame_ids
                        )
                    ],
                },
                "pricing_estimate": (
                    pricing_service.estimate(
                        provider_model,
                        segment.prompt,
                        {
                            **(plan.parameters or {}),
                            **(segment.parameters or {}),
                            "duration": segment.generation_duration,
                        },
                    )
                    if provider_model is not None
                    else {
                        "status": "unknown",
                        "currency": "CNY",
                        "amount": None,
                        "reason": "当前计划未绑定可计价的视频模型",
                    }
                ),
                "status": segment.status,
                "script_state": script_state(segment),
                "shots": [
                    {
                        "shot_id": link.shot_id,
                        "order": link.order,
                        "start_time": link.start_time,
                        "end_time": link.end_time,
                        "scene_id": shot_map[link.shot_id].scene_id if link.shot_id in shot_map else None,
                        "scene_name": scene_map[shot_map[link.shot_id].scene_id].name
                        if link.shot_id in shot_map and shot_map[link.shot_id].scene_id in scene_map
                        else None,
                        "shot_size": shot_map[link.shot_id].shot_size if link.shot_id in shot_map else None,
                        "camera_angle": shot_map[link.shot_id].camera_angle if link.shot_id in shot_map else None,
                        "camera_movement": shot_map[link.shot_id].camera_movement if link.shot_id in shot_map else None,
                        "action": shot_map[link.shot_id].action if link.shot_id in shot_map else None,
                        "dialogue": shot_map[link.shot_id].dialogue if link.shot_id in shot_map else None,
                        "audio_note": shot_map[link.shot_id].audio_note if link.shot_id in shot_map else None,
                    }
                    for link in links_by_segment[segment.id]
                ],
                "video_versions": [
                    {
                        "id": version.id,
                        "media_file_id": version.media_file_id,
                        "source_job_id": version.source_job_id,
                        "legacy_shot_video_version_id": version.legacy_shot_video_version_id,
                        "version": version_index,
                        "prompt": version.prompt,
                        "negative_prompt": version.negative_prompt,
                        "parameters": version.parameters or {},
                        "is_final": version.is_final and version.segment_id == segment.id,
                        "media_url": version.media_url,
                        "candidate_status": evidence_by_version[version.id]["status"],
                        "adoptable": evidence_by_version[version.id]["adoptable"],
                        "adoption_block_reason": evidence_by_version[version.id]["reason"],
                        "media_status": evidence_by_version[version.id]["media_status"],
                        "input_fingerprint": evidence_by_version[version.id]["input_fingerprint"],
                        "script_fingerprint": evidence_by_version[version.id]["script_fingerprint"],
                        "provider_model_id": evidence_by_version[version.id]["provider_model_id"],
                    }
                    for version_index, version in enumerate(versions_by_segment[segment.id], start=1)
                ],
            }
            for segment in segments
        ],
    }
