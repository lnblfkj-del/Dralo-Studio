"""Job 管理服务：任务删除、恢复、永久删除与批量操作。

本模块处理 Job 的回收站管理和批量管理操作，保留业务数据。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    BillingCall,
    Job,
    utcnow,
)
from app.services.job_concurrency_service import BATCH_PARENT_TARGETS, TERMINAL_STATUSES
from app.services.job_state_service import cancel_job, retry_job
from app.services.team_access import owner_scope


async def delete_job(session: AsyncSession, job: Job) -> int:
    """Move terminal history to the recycle bin without deleting assets or billing."""
    if job.status not in TERMINAL_STATUSES:
        raise ConflictError("活动任务不能删除，请先取消并等待状态更新")
    if job.deleted_at is not None:
        raise ConflictError("任务已经在回收站中")
    if job.parent_job_id is not None:
        raise ConflictError("批量子任务不能单独删除，请删除对应的父任务")
    job_id = job.id
    deleted_at = utcnow()
    job.deleted_at = deleted_at
    if job.target_type in BATCH_PARENT_TARGETS:
        await session.execute(
            update(Job).where(Job.parent_job_id == job.id).values(deleted_at=deleted_at)
        )
    await session.flush()
    return job_id


async def restore_job(session: AsyncSession, job: Job) -> Job:
    """Restore one root task and its child history from the recycle bin."""
    if job.deleted_at is None:
        raise ConflictError("任务不在回收站中")
    if job.parent_job_id is not None:
        raise ConflictError("批量子任务不能单独恢复，请恢复对应的父任务")
    job.deleted_at = None
    if job.target_type in BATCH_PARENT_TARGETS:
        await session.execute(
            update(Job).where(Job.parent_job_id == job.id).values(deleted_at=None)
        )
    await session.flush()
    return job


async def purge_job(session: AsyncSession, job: Job) -> list[int]:
    """Permanently remove recycled task history while preserving business data."""
    if job.deleted_at is None:
        raise ConflictError("任务必须先移入回收站才能永久删除")
    if job.status not in TERMINAL_STATUSES:
        raise ConflictError("活动任务不能永久删除")
    if job.parent_job_id is not None:
        raise ConflictError("批量子任务不能单独永久删除，请处理对应父任务")
    child_ids = list(
        (await session.scalars(select(Job.id).where(Job.parent_job_id == job.id))).all()
    )
    purged_ids = [job.id, *child_ids]
    now = utcnow()
    calls = list(
        (await session.scalars(select(BillingCall).where(BillingCall.job_id.in_(purged_ids)))).all()
    )
    job_by_id = {
        item.id: item
        for item in (
            await session.scalars(select(Job).where(Job.id.in_(purged_ids)))
        ).all()
    }
    from app.services.reference_parse_cleanup import references
    for source in job_by_id.values():
        if source.job_type == "source_parse":
            session.info.setdefault("reference_parse_cleanup", set()).update(references(source.payload, source.result))
    for call in calls:
        source = job_by_id.get(call.job_id)
        call.snapshot = {
            **(call.snapshot or {}),
            "purged_job": {
                "id": call.job_id,
                "parent_job_id": source.parent_job_id if source else None,
                "job_type": source.job_type if source else None,
                "target_type": source.target_type if source else None,
                "target_id": source.target_id if source else None,
                "status": source.status if source else None,
                "purged_at": now.isoformat(),
            },
        }
        call.job_id = None
    # Child rows are deleted explicitly for databases where self-referential
    # cascades are deferred; every other job reference uses SET NULL.
    if child_ids:
        await session.execute(delete(Job).where(Job.id.in_(child_ids)))
    await session.execute(delete(Job).where(Job.id == job.id))
    await session.flush()
    return purged_ids


async def pause_batch(session: AsyncSession, job: Job) -> Job:
    if job.parent_job_id is not None or job.target_type not in BATCH_PARENT_TARGETS:
        raise ConflictError("只有批次父任务可以暂停后续提交")
    if job.status in TERMINAL_STATUSES:
        raise ConflictError("已结束批次不能暂停")
    job.batch_paused_at = utcnow()
    await session.flush()
    return job


async def resume_batch(session: AsyncSession, job: Job) -> Job:
    if job.parent_job_id is not None or job.target_type not in BATCH_PARENT_TARGETS:
        raise ConflictError("只有批次父任务可以继续提交")
    if job.batch_paused_at is None:
        raise ConflictError("批次当前未暂停")
    if job.status in TERMINAL_STATUSES:
        raise ConflictError("已结束批次不能继续")
    job.batch_paused_at = None
    await session.flush()
    return job


async def bulk_manage_jobs(
    session: AsyncSession,
    owner_id: int,
    job_ids: list[int],
    action: str,
) -> list[dict[str, Any]]:
    """Apply a management action independently while preserving per-item errors."""
    if action not in {"cancel", "retry", "delete", "restore", "purge"}:
        raise ValidationError("不支持的批量任务操作")
    jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.id.in_(job_ids),
                    Job.deleted_at.is_not(None) if action in {"restore", "purge"} else Job.deleted_at.is_(None),
                )
            )
        ).all()
    )
    by_id = {job.id: job for job in jobs}
    selected = set(job_ids)
    results: list[dict[str, Any]] = []
    for job_id in job_ids:
        job = by_id.get(job_id)
        if job is None:
            results.append(
                {
                    "job_id": job_id,
                    "outcome": "failed",
                    "error_code": "NOT_FOUND",
                    "error_message": "任务不存在",
                }
            )
            continue
        if job.parent_job_id in selected:
            results.append(
                {
                    "job_id": job_id,
                    "outcome": "covered_by_parent",
                    "job_status": job.status,
                }
            )
            continue
        original_status = job.status
        try:
            async with session.begin_nested():
                if action == "cancel":
                    await cancel_job(session, job)
                    outcome = "cancelled"
                elif action == "retry":
                    await retry_job(session, job)
                    outcome = "queued"
                elif action == "restore":
                    await restore_job(session, job)
                    outcome = "restored"
                elif action == "purge":
                    await purge_job(session, job)
                    outcome = "purged"
                else:
                    await delete_job(session, job)
                    outcome = "deleted"
            results.append(
                {
                    "job_id": job_id,
                    "outcome": outcome,
                    "job_status": None if outcome in {"deleted", "purged"} else job.status,
                }
            )
        except AppError as exc:
            results.append(
                {
                    "job_id": job_id,
                    "outcome": "failed",
                    "job_status": original_status,
                    "error_code": exc.code,
                    "error_message": exc.message,
                }
            )
    return results
