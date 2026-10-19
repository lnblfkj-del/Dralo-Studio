"""Atomic, replayable edits to the active episode timeline."""

from sqlalchemy import select, update

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import EpisodeProduction, EpisodeProductionPlan, Job, VideoSegment
from app.services.segment_plan_core_service import get_active_plan
from app.services.segment_plan_write_service import _plan_input_segment, create_plan


async def delete_segments(session, episode, *, request_id, expected_plan_id,
                          expected_production_revision, segment_ids, confirmed):
    if not confirmed or not segment_ids or len(set(segment_ids)) != len(segment_ids):
        raise ValidationError("请选择不重复的片段并确认删除")
    operation = {"request_id": request_id, "expected_plan_id": expected_plan_id,
                 "expected_production_revision": expected_production_revision,
                 "segment_ids": sorted(segment_ids)}

    async def replay():
        plans = (await session.scalars(select(EpisodeProductionPlan).where(
            EpisodeProductionPlan.episode_id == episode.id,
            EpisodeProductionPlan.source_type == "manual",
        ))).all()
        for plan in plans:
            receipt = (plan.parameters or {}).get("delete_receipt")
            if receipt and receipt.get("request_id") == request_id:
                if receipt != operation:
                    raise ConflictError("请求编号已用于其他删除操作，请刷新后重新选择")
                # A delayed replay must not replace a newer edit in the client.
                return await get_active_plan(session, episode)
        return None

    recovered = await replay()
    if recovered is not None:
        return recovered
    # This conditional write takes the episode's DB write lock before reading
    # jobs or changing the timeline. A failed transaction also rolls it back.
    locked = await session.execute(update(EpisodeProduction).where(
        EpisodeProduction.episode_id == episode.id,
        EpisodeProduction.active_plan_id == expected_plan_id,
        EpisodeProduction.revision == expected_production_revision,
    ).values(revision=EpisodeProduction.revision).execution_options(synchronize_session=False))
    recovered = await replay()
    if recovered is not None:
        return recovered
    if locked.rowcount != 1:
        raise ConflictError("片段或制作设置已变化，请刷新后重新选择；本次未删除任何片段")
    current = await get_active_plan(session, episode)
    selected_ids = set(segment_ids)
    if not selected_ids.issubset({item["id"] for item in current["segments"]}):
        raise NotFoundError("所选片段不属于当前分集的当前版本，本次未删除任何片段")
    removed = [item for item in current["segments"] if item["id"] in selected_ids]
    surviving = [item for item in current["segments"] if item["id"] not in selected_ids]
    lineages = {item["lineage_key"] for item in removed}
    if any(((item.get("refs") or {}).get("continuity") or {}).get("source_lineage_key") in lineages
           for item in surviving):
        raise ConflictError("保留的片段依赖所选片段的尾帧，请先解除依赖；本次未删除任何片段")
    historical_ids = set((await session.scalars(select(VideoSegment.id).where(
        VideoSegment.episode_id == episode.id, VideoSegment.lineage_key.in_(lineages),
    ))).all())
    jobs = (await session.scalars(select(Job).where(
        Job.project_id == episode.project_id,
        Job.status.in_(("queued", "running", "processing", "downloading", "retrying")),
    ))).all()
    for job in jobs:
        payload = job.payload or {}
        frame = payload.get("segment_frame") or {}
        if ((job.target_type == "video_segment" and job.target_id in historical_ids)
                or frame.get("segment_id") in historical_ids
                or bool(historical_ids.intersection(payload.get("segment_ids") or []))):
            raise ConflictError(f"所选片段仍有任务 #{job.id} 执行中，请停止或等待完成；本次未删除任何片段")
    parameters = {**current.get("parameters", {})}
    excluded = set(parameters.get("excluded_shot_ids") or [])
    excluded.update(link["shot_id"] for item in removed for link in item["shots"])
    parameters.update(operation="delete_selected")
    return await create_plan(
        session, episode, expected_production_revision=expected_production_revision,
        provider_model_id=current["provider_model_id"],
        model_capability_snapshot=current.get("model_capability_snapshot") or {},
        parameters=parameters, status="draft",
        segments=[_plan_input_segment(item) for item in surviving],
        source_type="manual", parent_plan_id=current["id"], excluded_shot_ids=sorted(excluded),
        delete_receipt=operation,
    )
