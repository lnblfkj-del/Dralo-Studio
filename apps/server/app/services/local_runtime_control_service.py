"""Guard and observe controlled restarts of the local general Worker."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DOWNLOADING,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RUNNING,
    Job,
    utcnow,
)
from app.runtime import control_channel
from app.services import worker_runtime_service

ACTIVE_STATUSES = {
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_DOWNLOADING,
}
TERMINAL_FAILURE_STATUSES = {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
TERMINAL_RESTART_STATES = {"completed", "failed", "timed_out"}


def _requires_result_review(job: Job) -> bool:
    if job.status not in TERMINAL_FAILURE_STATUSES:
        return False
    if (job.resolution or {}).get("status") in {"superseded", "replacement_pending"}:
        return False
    payload = job.payload or {}
    recovery = dict(payload.get("recovery_summary") or {})
    if int(recovery.get("channel_check_required") or 0) > 0:
        return True
    text_submission = dict(payload.get("text_submission") or {})
    if text_submission.get("status") == "submitted" and not text_submission.get("response_received"):
        return True
    video_submission = dict(payload.get("video_submission") or {})
    return bool(video_submission.get("started") and not video_submission.get("id"))


async def guard_status(session) -> dict:
    active_jobs = list((await session.scalars(select(Job).where(
        Job.deleted_at.is_(None),
        Job.status.in_(ACTIVE_STATUSES),
        Job.worker_id.is_not(None),
        Job.lease_expires_at >= utcnow(),
    ).order_by(Job.id))).all())
    failed_jobs = list((await session.scalars(select(Job).where(
        Job.deleted_at.is_(None),
        Job.status.in_(TERMINAL_FAILURE_STATUSES),
    ).order_by(Job.id))).all())
    review_ids = [job.id for job in failed_jobs if _requires_result_review(job)]
    available = control_channel.supervisor_available()
    local = settings.runtime_execution_location == "local"
    return {
        "allowed": bool(local and available and not active_jobs and not review_ids),
        "execution_location": settings.runtime_execution_location,
        "supervisor_available": available,
        "active_job_ids": [job.id for job in active_jobs],
        "result_review_job_ids": review_ids,
        "active_jobs": len(active_jobs),
        "result_review_jobs": len(review_ids),
    }


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def request_worker_restart(session, *, expected_policy_revision: int, requested_by: int) -> dict:
    guard = await guard_status(session)
    if guard["execution_location"] != "local":
        raise ConflictError("当前不是本地执行环境，不能通过此入口重启 Worker")
    if not guard["supervisor_available"]:
        raise ConflictError("Supervisor 未运行，无法安全重启 Worker")
    if guard["active_jobs"]:
        raise ConflictError(
            "Worker 正在执行任务，任务结束后才能重启",
            details={"active_job_ids": guard["active_job_ids"]},
        )
    if guard["result_review_jobs"]:
        raise ConflictError(
            "存在远端结果或费用待核对任务，处理后才能重启 Worker",
            details={"result_review_job_ids": guard["result_review_job_ids"]},
        )
    current_state = control_channel.read_json(control_channel.STATE_PATH) or {}
    if current_state.get("request_id") and current_state.get("state") not in TERMINAL_RESTART_STATES:
        current_state = await restart_status(session, str(current_state["request_id"]))
    if current_state.get("state") not in {None, *TERMINAL_RESTART_STATES}:
        raise ConflictError("已有 Worker 重启请求正在处理")
    request_id = uuid4().hex
    requested_at = control_channel.utc_iso()
    command = {
        "schema_version": 1,
        "request_id": request_id,
        "action": "restart_worker",
        "requested_at": requested_at,
        "requested_by": requested_by,
        "expected_policy_revision": expected_policy_revision,
    }
    state = {**command, "state": "requested", "updated_at": requested_at}
    control_channel.write_json(control_channel.STATE_PATH, state)
    try:
        control_channel.create_command(command)
    except FileExistsError as exc:
        state.update({"state": "failed", "message": "已有控制命令等待 Supervisor 处理", "updated_at": control_channel.utc_iso()})
        control_channel.write_json(control_channel.STATE_PATH, state)
        raise ConflictError("已有 Worker 控制命令等待处理") from exc
    return state


async def restart_status(session, request_id: str) -> dict:
    state = control_channel.read_json(control_channel.STATE_PATH)
    if not state or state.get("request_id") != request_id:
        raise NotFoundError("Worker 重启请求不存在")
    requested_at = _parse_time(state.get("requested_at"))
    expected_revision = int(state.get("expected_policy_revision") or 0)
    runtime = await worker_runtime_service.status(session, include_workers=True)
    synchronized_worker = None
    if requested_at is not None:
        for worker in runtime.get("workers", []):
            started_at = worker.get("started_at")
            if (
                worker.get("kind") == "general"
                and worker.get("policy_revision") == expected_revision
                and isinstance(started_at, datetime)
                and (started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at.astimezone(UTC)) >= requested_at
            ):
                synchronized_worker = worker
                break
    result = dict(state)
    if synchronized_worker is not None:
        result.update({
            "state": "completed",
            "worker_id": synchronized_worker["id"],
            "completed_at": control_channel.utc_iso(),
            "updated_at": control_channel.utc_iso(),
        })
    elif (
        result.get("state") not in TERMINAL_RESTART_STATES
        and requested_at is not None
        and datetime.now(UTC) - requested_at > timedelta(seconds=60)
    ):
        result.update({"state": "timed_out", "message": "新 Worker 未在 60 秒内完成心跳同步", "updated_at": control_channel.utc_iso()})
    if result.get("state") in TERMINAL_RESTART_STATES:
        control_channel.write_json(
            control_channel.STATE_PATH,
            {key: value for key, value in result.items() if key != "runtime"},
        )
    result["runtime"] = runtime
    return result
