"""Read-only projection for the existing episode page; no queue or model calls."""

from sqlalchemy import select

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import EpisodeProduction, EpisodeProductionPlan, Job, Provider, ProviderModel
from app.models.episode_planning import EpisodePlanningRecord
from app.services.episode_planning_assembly import read_assembly
from app.services.episode_planning_storage import read_content_analysis, read_plan
from app.services.episode_planning_workflow import TARGET_RUN


async def read_result(
    session, episode, job_id: int, *, include_video_preview: bool = False
) -> dict:
    job = await session.get(Job, job_id)
    if (
        job is None
        or job.deleted_at is not None
        or job.target_type != TARGET_RUN
        or job.project_id != episode.project_id
        or job.target_id != episode.id
        or job.workspace_id != episode.workspace_id
        or job.owner_id != episode.owner_id
    ):
        raise NotFoundError("本集规划任务不存在")
    result = job.result or {}
    assembly = None
    video_preview = None
    if result.get("assembly") is not None:
        record = await session.get(EpisodePlanningRecord, job.payload.get("planning_record_id"))
        if (
            record is None
            or record.episode_id != episode.id
            or record.workspace_id != episode.workspace_id
            or record.execution_epoch != job.payload["epoch"]
        ):
            raise ConflictError("冻结计划记录不匹配，不能展示未经核验的结果")
        try:
            assembly = read_assembly(
                read_plan(record), read_content_analysis(record), result["assembly"]
            )
        except ValueError as exc:
            raise ConflictError("规划结果完整性校验失败，请保留任务记录后检查") from exc
        if include_video_preview:
            from app.core.config import settings
            from app.services.episode_planning_capability import load_capability
            from app.services.episode_planning_video_preview import compile_preview

            plan = read_plan(record)
            try:
                if plan.execution_epoch != settings.episode_planning_epoch:
                    raise ConflictError("执行批次已变化，请重新核对计划")
                if plan.script_revision != episode.script_revision:
                    raise ConflictError("剧本已修改，请先重新核对片段计划")
                capability = await load_capability(session, plan.capability.provider_model_id)
                model = await session.get(ProviderModel, capability.provider_model_id)
                provider = await session.get(Provider, model.provider_id)
                video_preview = compile_preview(
                    plan,
                    read_content_analysis(record),
                    assembly,
                    provider=provider,
                    model=model,
                    current_capability=capability,
                )
            except AppError as exc:
                # Historical text remains readable even after a route is removed.
                video_preview = {"blockers": [exc.message], "video_submission_ready": False}
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id))
    projection_id = (job.payload.get("production_projection") or {}).get("plan_id")
    projected = await session.get(EpisodeProductionPlan, projection_id) if projection_id else None
    return {
        "job_id": job.id,
        "status": job.status,
        "stage": result.get("stage"),
        "progress": job.progress,
        "completed": result.get("completed", 0),
        "total": result.get("total", 0),
        "script_revision": job.payload["request_spec"]["script_revision"],
        "current_script_revision": episode.script_revision,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        "assembly": assembly,
        "has_frozen_plan": bool(job.payload.get("planning_record_id")),
        "video_preview": video_preview,
        "video_submission_ready": False,
        "production": {
            "revision": production.revision if production else 0,
            "active_plan_id": production.active_plan_id if production else None,
            "projected_plan_id": projected.id if projected else None,
            "projected_version": projected.version if projected else None,
            "active": bool(projected and production and production.active_plan_id == projected.id),
        },
    }
