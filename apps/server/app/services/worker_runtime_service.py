"""Worker lifecycle records used by health checks and task monitoring."""

import os
import socket
from datetime import timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    Job,
    WorkerRuntime,
    utcnow,
)

RUNTIME_VERSION = "0.5.0"


def heartbeat_grace_seconds() -> int:
    return max(15, int(settings.job_poll_interval_seconds * 5))


async def _stop_stale(session, current_worker_id: str, now) -> None:
    await session.execute(
        update(WorkerRuntime)
        .where(
            WorkerRuntime.worker_id != current_worker_id,
            WorkerRuntime.status == "online",
            WorkerRuntime.heartbeat_at < now - timedelta(seconds=heartbeat_grace_seconds()),
        )
        .values(status="stopped", active_jobs=0, stopped_at=now)
    )


async def register(session, worker_id: str, *, local_media_only: bool) -> WorkerRuntime:
    from app.services.execution_policy_service import current_snapshot

    runtime = await session.get(WorkerRuntime, worker_id)
    now = utcnow()
    await _stop_stale(session, worker_id, now)
    capabilities = ["media_process", "export", "edit_project_export", "edit_project_archive", "edit_project_video_viewport"] if local_media_only else ["text", "script", "storyboard", "image", "video", "tts", "media_process", "export", "edit_project_export", "edit_project_archive", "edit_project_video_viewport"]
    max_concurrency = 1 if local_media_only else settings.worker_concurrency
    policy_revision = int(current_snapshot()["revision"])
    if runtime is None:
        runtime = WorkerRuntime(worker_id=worker_id, kind="local_media" if local_media_only else "general", status="online", hostname=socket.gethostname(), pid=os.getpid(), version=RUNTIME_VERSION, policy_revision=policy_revision, capabilities=capabilities, max_concurrency=max_concurrency, active_jobs=0, started_at=now, heartbeat_at=now)
        session.add(runtime)
    else:
        runtime.status = "online"
        runtime.hostname = socket.gethostname()
        runtime.pid = os.getpid()
        runtime.version = RUNTIME_VERSION
        runtime.policy_revision = policy_revision
        runtime.capabilities = capabilities
        runtime.max_concurrency = max_concurrency
        runtime.active_jobs = 0
        runtime.started_at = now
        runtime.heartbeat_at = now
        runtime.stopped_at = None
    await session.flush()
    return runtime


async def heartbeat(session, worker_id: str, *, active_jobs: int = 0) -> bool:
    runtime = await session.get(WorkerRuntime, worker_id)
    if runtime is None:
        return False
    runtime.status = "online"
    runtime.heartbeat_at = utcnow()
    await _stop_stale(session, worker_id, runtime.heartbeat_at)
    runtime.active_jobs = max(0, min(active_jobs, runtime.max_concurrency))
    await session.flush()
    return True


async def stop(session, worker_id: str) -> None:
    runtime = await session.get(WorkerRuntime, worker_id)
    if runtime is not None:
        runtime.status = "stopped"
        runtime.active_jobs = 0
        runtime.stopped_at = utcnow()
        runtime.heartbeat_at = runtime.stopped_at
        await session.flush()


async def status(session, *, include_workers: bool = False) -> dict:
    cutoff = utcnow() - timedelta(seconds=heartbeat_grace_seconds())
    online = list((await session.scalars(select(WorkerRuntime).where(WorkerRuntime.status == "online", WorkerRuntime.heartbeat_at >= cutoff).order_by(WorkerRuntime.worker_id))).all())
    compatible = [item for item in online if item.version == RUNTIME_VERSION]
    queued_types = list((await session.scalars(select(Job.job_type).where(Job.status == JOB_STATUS_QUEUED))).all())
    remote_types = list((await session.scalars(select(Job.job_type).where(
        Job.status == JOB_STATUS_PROCESSING,
        Job.execution_phase == "poll",
        Job.worker_id.is_(None),
    ))).all())
    capabilities = {capability for item in compatible for capability in item.capabilities}
    blocked = sum(
        1 for job_type in [*queued_types, *remote_types]
        if job_type not in capabilities
    )
    result = {
        "execution_location": settings.runtime_execution_location,
        "status": "online" if compatible else ("version_mismatch" if online else "offline"),
        "ready": bool(compatible),
        "active_workers": len(online),
        "compatible_workers": len(compatible),
        "version_mismatch_workers": len(online) - len(compatible),
        "runtime_version": RUNTIME_VERSION,
        "concurrency_preset": settings.job_concurrency_preset,
        "concurrency_limits": settings.concurrency_limits,
        "total_capacity": sum(item.max_concurrency for item in compatible),
        "active_jobs": sum(item.active_jobs for item in compatible),
        "available_capacity": sum(
            max(0, item.max_concurrency - item.active_jobs) for item in compatible
        ),
        "queued_jobs": len(queued_types),
        "remote_jobs": len(remote_types),
        "blocked_queued_jobs": blocked,
        "can_process_queue": bool(compatible) and blocked == 0,
        "heartbeat_grace_seconds": heartbeat_grace_seconds(),
    }
    if include_workers:
        result["workers"] = [{"id": item.worker_id, "kind": item.kind, "version": item.version, "policy_revision": item.policy_revision, "capabilities": item.capabilities, "max_concurrency": item.max_concurrency, "active_jobs": item.active_jobs, "started_at": item.started_at, "heartbeat_at": item.heartbeat_at} for item in online]
    return result
