"""Job 并发控制服务：模型限流、成功恢复与并发窗口管理。

本模块处理 ProviderModel 的并发槽位管理，包括 429 限流降速和成功恢复。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Job, ProviderModel, utcnow


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job_provider_model_id(job: Job) -> int | None:
    value = (job.payload or {}).get("provider_model_id")
    return int(value) if isinstance(value, int) and value > 0 else None


async def record_model_rate_limit(
    session: AsyncSession,
    job: Job,
    *,
    retry_after_seconds: int | None = None,
) -> datetime | None:
    """Atomically shrink a model window shared by every Worker process."""
    model_id = _job_provider_model_id(job)
    if model_id is None:
        return None
    model = await session.scalar(
        select(ProviderModel).where(ProviderModel.id == model_id).with_for_update()
    )
    if model is None:
        return None
    now = utcnow()
    model.rate_limit_hits = min(30, model.rate_limit_hits + 1)
    model.success_streak = 0
    model.effective_concurrency = max(1, (model.effective_concurrency + 1) // 2)
    exponential = settings.model_rate_limit_base_seconds * (2 ** min(model.rate_limit_hits - 1, 5))
    delay = min(
        settings.model_rate_limit_max_seconds,
        max(settings.model_rate_limit_base_seconds, retry_after_seconds or 0, exponential),
    )
    model.last_rate_limited_at = now
    model.rate_limit_until = now + timedelta(seconds=delay)
    await session.flush()
    return model.rate_limit_until


async def record_model_success(session: AsyncSession, job_id: int) -> None:
    """Recover one slot only after a configurable run of successful completions."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    model_id = _job_provider_model_id(job)
    if model_id is None:
        return
    model = await session.scalar(
        select(ProviderModel).where(ProviderModel.id == model_id).with_for_update()
    )
    if model is None or model.effective_concurrency >= model.max_concurrency:
        if model is not None:
            model.effective_concurrency = min(model.effective_concurrency, model.max_concurrency)
            model.success_streak = 0
            if model.effective_concurrency == model.max_concurrency:
                model.rate_limit_hits = 0
                model.rate_limit_until = None
        return
    now = utcnow()
    if _aware_utc(model.rate_limit_until) is not None and _aware_utc(model.rate_limit_until) > now:
        return
    model.success_streak += 1
    if model.success_streak >= settings.model_recovery_successes:
        model.effective_concurrency += 1
        model.success_streak = 0
        model.rate_limit_hits = max(0, model.rate_limit_hits - 1)
        if model.effective_concurrency >= model.max_concurrency:
            model.effective_concurrency = model.max_concurrency
            model.rate_limit_hits = 0
            model.rate_limit_until = None
    await session.flush()


# 常量定义（从原 job_service.py 迁移）
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DOWNLOADING,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXPORT,
)

TERMINAL_STATUSES = {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
BATCH_PARENT_TARGETS = {
    "video_batch",
    "episode_video_batch",
    "script_study_batch_group",
    "script_asset_breakdown_group",
    "episode_script_batch",
    "asset_image_batch",
    "segment_first_frame_batch",
    "episode_director_pipeline",
}
LOCAL_JOB_TYPES = {"media_process", "source_parse", JOB_TYPE_EXPORT}
ACTIVE_LEASE_STATUSES = {
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_DOWNLOADING,
}
REMOTE_EXECUTION_PHASES = {"poll", "download"}
