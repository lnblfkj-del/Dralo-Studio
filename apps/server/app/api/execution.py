"""Administrator execution policy and Worker status endpoints."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AdminUser, SessionDep
from app.services import (
    execution_policy_service,
    local_runtime_control_service,
    worker_runtime_service,
)

router = APIRouter(prefix="/execution-settings", tags=["execution-settings"])


class ExecutionPolicyUpdate(BaseModel):
    revision: int
    policy: dict[str, Any]


async def _response(session: SessionDep, item) -> dict:
    from app.core.workspace_context import system_scope
    with system_scope():
        runtime = await worker_runtime_service.status(session, include_workers=True)
        restart_guard = await local_runtime_control_service.guard_status(session)
    applied = {worker.get("policy_revision") for worker in runtime.get("workers", [])}
    return {
        "revision": item.revision,
        "policy": item.policy,
        "updated_at": item.updated_at,
        "updated_by": item.updated_by,
        "restart_required": bool(applied and applied != {item.revision}),
        "restart_guard": restart_guard,
        "runtime": runtime,
    }


@router.get("")
async def get_execution_settings(session: SessionDep, _admin: AdminUser):
    item = await execution_policy_service.get_or_create(session)
    await session.commit()
    return await _response(session, item)


@router.put("")
async def update_execution_settings(payload: ExecutionPolicyUpdate, session: SessionDep, admin: AdminUser):
    item = await execution_policy_service.update_policy(session, payload.revision, payload.policy, admin.id)
    await session.commit()
    return await _response(session, item)


@router.post("/worker/restart", status_code=202)
async def restart_worker(session: SessionDep, admin: AdminUser):
    from app.core.config import settings
    from app.core.errors import ConflictError
    if settings.runtime_execution_location == "cloud":
        raise ConflictError("云端 Worker 由服务器进程管理器维护，不接受客户端本机重启命令")
    item = await execution_policy_service.get_or_create(session)
    return await local_runtime_control_service.request_worker_restart(
        session,
        expected_policy_revision=item.revision,
        requested_by=admin.id,
    )


@router.get("/worker/restart/{request_id}")
async def get_worker_restart(request_id: str, session: SessionDep, _admin: AdminUser):
    from app.core.workspace_context import system_scope
    with system_scope():
        return await local_runtime_control_service.restart_status(session, request_id)
