"""Lease, progress, and remote deadline helpers for the worker."""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import RemoteVideoTimeoutError
from app.models import Job, utcnow
from app.services import job_service, worker_runtime_service

logger = logging.getLogger(__name__)

def _remote_video_started_at(job: Job, submission: dict) -> datetime:
    raw = submission.get("started_at")
    if isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        except ValueError:
            pass
    value = job.started_at or job.created_at or utcnow()
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _assert_remote_video_within_deadline(job: Job, submission: dict) -> None:
    timeout = int(submission.get("timeout_seconds") or settings.video_remote_timeout_seconds)
    elapsed = (utcnow() - _remote_video_started_at(job, submission)).total_seconds()
    if elapsed >= timeout:
        raise RemoteVideoTimeoutError(
            f"远端视频任务已追踪超过 {timeout} 秒，系统停止自动轮询；安全续跑会继续查询原任务，不会重新提交"
        )

async def _update_runtime_progress(
    job_id: int,
    worker_id: str,
    *,
    stage: str,
    draft: str | None = None,
    streaming: bool | None = None,
    first_chunk_ms: int | None = None,
) -> bool:
    """Persist an observable real phase and mirror it to a sequential parent."""
    async with SessionLocal() as session:
        current = await session.get(Job, job_id)
        if (
            current is None
            or current.worker_id != worker_id
            or current.status in job_service.TERMINAL_STATUSES
        ):
            return False
        previous = dict((current.payload or {}).get("runtime_progress") or {})
        runtime = {
            **previous,
            "stage": stage,
            "sequence": int(previous.get("sequence") or 0) + 1,
            "episode_number": dict((current.payload or {}).get("parameters") or {}).get("episode_number"),
        }
        if current.started_at is not None:
            runtime["episode_started_at"] = current.started_at.isoformat()
        if draft is not None:
            runtime["draft"] = draft[-100_000:]
            runtime["received_chars"] = len(draft)
        if streaming is not None:
            runtime["streaming"] = streaming
        if first_chunk_ms is not None:
            runtime["first_chunk_ms"] = first_chunk_ms
        current.payload = {**(current.payload or {}), "runtime_progress": runtime}
        if current.parent_job_id is not None:
            parent = await session.get(Job, current.parent_job_id)
            if parent is not None and parent.target_type == "episode_script_batch":
                if parent.started_at is None:
                    parent.started_at = current.started_at or utcnow()
                parent.payload = {**(parent.payload or {}), "runtime_progress": runtime}
        await session.commit()
        return True


async def heartbeat(job_id: int, worker_id: str) -> None:
    while True:
        await asyncio.sleep(max(1, settings.job_lease_seconds // 3))
        async with SessionLocal() as session:
            active = await job_service.renew_lease(session, job_id, worker_id)
            await session.commit()
        if not active:
            return


async def runtime_heartbeat(
    worker_id: str, active_tasks: set[asyncio.Task]
) -> None:
    while True:
        await asyncio.sleep(max(2, worker_runtime_service.heartbeat_grace_seconds() // 3))
        try:
            async with SessionLocal() as session:
                await worker_runtime_service.heartbeat(
                    session, worker_id, active_jobs=len(active_tasks)
                )
                await session.commit()
        except Exception:
            logger.exception("Worker 心跳写入失败，将在下一周期重试 worker_id=%s", worker_id)
