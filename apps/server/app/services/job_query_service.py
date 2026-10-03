"""Job 查询服务：任务列表、详情与诊断。

本模块处理 Job 的查询逻辑，包括列表筛选、分页统计和运行诊断。
"""

from typing import Any

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_SUCCEEDED,
    Job,
    Provider,
    ProviderModel,
    utcnow,
)
from app.services.job_concurrency_service import ACTIVE_LEASE_STATUSES, REMOTE_EXECUTION_PHASES, TERMINAL_STATUSES, _aware_utc, _job_provider_model_id
from app.services.team_access import owner_scope, same_team


async def get_job(
    session: AsyncSession, job_id: int, owner_id: int, *, include_deleted: bool = False
) -> Job:
    filters = [Job.id == job_id, owner_scope(Job.owner_id, owner_id)]
    if not include_deleted:
        filters.append(Job.deleted_at.is_(None))
    job = await session.scalar(select(Job).where(*filters))
    if job is None:
        raise NotFoundError("任务不存在")
    return job


async def list_child_jobs(
    session: AsyncSession, parent: Job, owner_id: int
) -> list[Job]:
    from app.services.job_concurrency_service import BATCH_PARENT_TARGETS
    if not await same_team(session, parent.owner_id, owner_id) or parent.target_type not in BATCH_PARENT_TARGETS:
        raise NotFoundError("批量任务不存在")
    return list(
        (
            await session.scalars(
                select(Job)
                .where(Job.parent_job_id == parent.id, owner_scope(Job.owner_id, owner_id))
                .order_by(Job.id)
            )
        ).all()
    )


def _job_list_filters(
    owner_id: int,
    *,
    project_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    search: str | None = None,
    recycled: bool = False,
) -> list[Any]:
    filters: list[Any] = [owner_scope(Job.owner_id, owner_id)]
    filters.append(Job.deleted_at.is_not(None) if recycled else Job.deleted_at.is_(None))
    if project_id is not None:
        filters.append(Job.project_id == project_id)
    unresolved_failure = or_(
        Job.payload["resolution"]["status"].as_string().is_(None),
        Job.payload["resolution"]["status"].as_string() != "superseded",
    )
    if status == "active":
        filters.append(Job.status.in_(ACTIVE_LEASE_STATUSES))
    elif status == "attention":
        filters.append(or_(
            Job.status.in_({*ACTIVE_LEASE_STATUSES, JOB_STATUS_QUEUED, JOB_STATUS_RETRYING}),
            and_(Job.status == JOB_STATUS_FAILED, unresolved_failure),
        ))
    elif status == "queued":
        filters.append(Job.status.in_({JOB_STATUS_QUEUED, JOB_STATUS_RETRYING}))
    elif status and status != "all":
        filters.append(Job.status == status)
    type_groups = {
        "text": {"text", "script", "storyboard"},
        "image": {"image"},
        "video": {"video", "export"},
        "audio": {"audio", "tts"},
    }
    if job_type and job_type != "all":
        if job_type == "other":
            known_types = set().union(*type_groups.values())
            filters.append(Job.job_type.not_in(known_types))
        else:
            filters.append(Job.job_type.in_(type_groups.get(job_type, {job_type})))
    keyword = (search or "").strip().lower()
    if keyword:
        pattern = f"%{keyword}%"
        filters.append(or_(
            func.lower(cast(Job.id, String)).like(pattern),
            func.lower(cast(Job.project_id, String)).like(pattern),
            func.lower(Job.job_type).like(pattern),
            func.lower(func.coalesce(Job.provider, "")).like(pattern),
            func.lower(func.coalesce(Job.model, "")).like(pattern),
            func.lower(func.coalesce(Job.error_message, "")).like(pattern),
        ))
    return filters


async def list_jobs(
    session: AsyncSession,
    owner_id: int,
    *,
    offset: int = 0,
    limit: int = 50,
    project_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    search: str | None = None,
    sort: str = "newest",
    recycled: bool = False,
) -> dict[str, Any]:
    filters = _job_list_filters(
        owner_id,
        project_id=project_id,
        status=status,
        job_type=job_type,
        search=search,
        recycled=recycled,
    )
    order = Job.created_at.asc() if sort == "oldest" else Job.created_at.desc()
    total = int(await session.scalar(select(func.count(Job.id)).where(*filters)) or 0)
    items = list((await session.scalars(
        select(Job).where(*filters).order_by(order, Job.id.desc()).offset(offset).limit(limit)
    )).all())

    base = [owner_scope(Job.owner_id, owner_id), Job.deleted_at.is_(None)]
    if project_id is not None:
        base.append(Job.project_id == project_id)
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    unresolved_failure = or_(
        Job.payload["resolution"]["status"].as_string().is_(None),
        Job.payload["resolution"]["status"].as_string() != "superseded",
    )
    row = (await session.execute(select(
        func.count(Job.id),
        func.sum(case((Job.status.in_(ACTIVE_LEASE_STATUSES), 1), else_=0)),
        func.sum(case((Job.status.in_({JOB_STATUS_QUEUED, JOB_STATUS_RETRYING}), 1), else_=0)),
        func.sum(case((Job.status == JOB_STATUS_SUCCEEDED, 1), else_=0)),
        func.sum(case((and_(Job.status == JOB_STATUS_FAILED, unresolved_failure), 1), else_=0)),
        func.sum(case((Job.status == JOB_STATUS_CANCELLED, 1), else_=0)),
        func.sum(case((and_(Job.status == JOB_STATUS_SUCCEEDED, Job.finished_at >= today), 1), else_=0)),
    ).where(*base))).one()
    recycle_filters = [owner_scope(Job.owner_id, owner_id), Job.deleted_at.is_not(None)]
    if project_id is not None:
        recycle_filters.append(Job.project_id == project_id)
    recycled_count = int(await session.scalar(select(func.count(Job.id)).where(*recycle_filters)) or 0)
    values = [int(value or 0) for value in row]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "stats": {
            "total": values[0], "active": values[1], "queued": values[2],
            "succeeded": values[3], "failed": values[4], "cancelled": values[5],
            "today_completed": values[6], "recycled": recycled_count,
        },
    }


async def matching_job_ids(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    search: str | None = None,
    recycled: bool = False,
) -> list[int]:
    """Resolve a server-side selection so pagination cannot omit matching tasks."""
    filters = _job_list_filters(
        owner_id,
        project_id=project_id,
        status=status,
        job_type=job_type,
        search=search,
        recycled=recycled,
    )
    return list((await session.scalars(select(Job.id).where(*filters).order_by(Job.id))).all())


def retry_block_reason(job: Job) -> str | None:
    return job.retry_block_reason


async def job_diagnostic(session: AsyncSession, job: Job) -> dict[str, Any]:
    """Return a safe explanation without exposing prompts, keys or provider URLs."""
    from app.services import worker_runtime_service

    model_runtime = None
    runtime = await worker_runtime_service.status(session, include_workers=True)
    worker_online = bool(runtime.get("ready"))
    if job.worker_id:
        worker_online = any(
            item["id"] == job.worker_id for item in runtime.get("workers", [])
        )
    queue_reason: str | None = None
    if (
        job.status == JOB_STATUS_PROCESSING
        and job.execution_phase == "poll"
        and job.worker_id is None
    ):
        queue_reason = "remote_processing"
    elif job.status == JOB_STATUS_RETRYING:
        queue_reason = "retry_backoff"
    elif job.status == JOB_STATUS_QUEUED:
        compatible = [
            item
            for item in runtime.get("workers", [])
            if item["version"] == worker_runtime_service.RUNTIME_VERSION
            and job.job_type in item["capabilities"]
        ]
        if not compatible:
            queue_reason = (
                "worker_version_mismatch"
                if runtime["version_mismatch_workers"]
                else "no_compatible_worker"
            )
        elif runtime["available_capacity"] <= 0:
            queue_reason = "worker_capacity_full"
        else:
            now = utcnow()
            active_filter = (
                or_(
                    and_(
                        Job.status.in_(ACTIVE_LEASE_STATUSES),
                        Job.worker_id.is_not(None),
                        Job.lease_expires_at >= now,
                    ),
                    and_(
                        Job.status == JOB_STATUS_PROCESSING,
                        Job.execution_phase.in_(REMOTE_EXECUTION_PHASES),
                    ),
                ),
            )
            global_active = await session.scalar(
                select(func.count(Job.id)).where(*active_filter)
            )
            type_active = await session.scalar(
                select(func.count(Job.id)).where(
                    *active_filter, Job.job_type == job.job_type
                )
            )
            provider_active = 0
            provider_limit = None
            model_active = 0
            if job.provider_id is not None:
                provider_active = await session.scalar(
                    select(func.count(Job.id)).where(
                        *active_filter, Job.provider_id == job.provider_id
                    )
                )
                provider_limit = await session.scalar(
                    select(Provider.max_concurrency).where(
                        Provider.id == job.provider_id
                    )
                )
            provider_model_id = _job_provider_model_id(job)
            if provider_model_id is not None:
                model = await session.get(ProviderModel, provider_model_id)
                model_active = await session.scalar(
                    select(func.count(Job.id)).where(
                        *active_filter,
                        Job.payload["provider_model_id"].as_integer() == provider_model_id,
                    )
                )
                if model is not None:
                    model_runtime = {
                        "model_id": model.id,
                        "configured_limit": model.max_concurrency,
                        "effective_limit": model.effective_concurrency,
                        "active": int(model_active or 0),
                        "rate_limit_until": model.rate_limit_until,
                        "rate_limit_hits": model.rate_limit_hits,
                    }
            if int(global_active or 0) >= settings.job_global_concurrency:
                queue_reason = "global_capacity_full"
            elif int(type_active or 0) >= settings.job_type_concurrency(job.job_type):
                queue_reason = "job_type_capacity_full"
            elif provider_limit is not None and int(provider_active or 0) >= provider_limit:
                queue_reason = "provider_capacity_full"
            elif model_runtime and _aware_utc(model_runtime["rate_limit_until"]) and _aware_utc(model_runtime["rate_limit_until"]) > now:
                queue_reason = "model_rate_limit_cooldown"
            elif model_runtime and int(model_active or 0) >= model_runtime["effective_limit"]:
                queue_reason = "model_capacity_full"
            else:
                queue_reason = "ready_to_claim"
    return {
        "job_id": job.id,
        "status": job.status,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "queue_reason": queue_reason,
        "worker_online": worker_online,
        "cancel_allowed": job.status not in TERMINAL_STATUSES,
        "retry_allowed": retry_block_reason(job) is None,
        "retry_block_reason": retry_block_reason(job),
        "paid_recall_allowed": job.paid_recall_allowed,
        "delete_allowed": job.status in TERMINAL_STATUSES
        and job.parent_job_id is None,
        "execution_info": job.execution_info,
        "model_runtime": model_runtime,
    }


# 需要从原文件导入的辅助函数
from app.core.config import settings
