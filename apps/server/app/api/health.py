"""健康检查。无需登录，供 Nginx 与部署脚本探活。"""

import shutil

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, literal

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.storage_safety import require_storage_capacity
from app.services import worker_runtime_service

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app_env: str
    database: str
    storage_free_gb: float
    storage_warning: str | None = None
    execution: dict


@router.get("/health", response_model=HealthResponse)
async def health(session: SessionDep) -> HealthResponse:
    """返回服务、数据库与磁盘状态。

    磁盘余量在响应中给出，便于及早发现存储水位问题（/data 空间有限）。
    """
    try:
        from app.core.workspace_context import system_scope
        with system_scope():
            await session.execute(select(literal(1)))
            database = "ok"
            execution = await worker_runtime_service.status(session)
    except Exception:  # noqa: BLE001 - 探活接口不应因数据库异常而 500
        database = "error"
        execution = {
            "execution_location": settings.runtime_execution_location,
            "status": "unavailable",
            "ready": False,
            "active_workers": 0,
            "compatible_workers": 0,
            "version_mismatch_workers": 0,
            "runtime_version": worker_runtime_service.RUNTIME_VERSION,
            "concurrency_preset": settings.job_concurrency_preset,
            "concurrency_limits": settings.concurrency_limits,
            "total_capacity": 0,
            "active_jobs": 0,
            "available_capacity": 0,
            "queued_jobs": 0,
            "remote_jobs": 0,
            "blocked_queued_jobs": 0,
            "can_process_queue": False,
            "heartbeat_grace_seconds": worker_runtime_service.heartbeat_grace_seconds(),
        }

    storage_path = settings.storage_path
    require_storage_capacity(settings, target=storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(storage_path).free
    execution_healthy = (
        execution["can_process_queue"]
        and execution["version_mismatch_workers"] == 0
    )
    return HealthResponse(
        status="ok" if database == "ok" and execution_healthy else "degraded",
        app_env=settings.app_env,
        database=database,
        storage_free_gb=round(free_bytes / 1024**3, 2),
        storage_warning="存储剩余空间不足 1 GiB，请及时清理回收站。" if free_bytes < 1024**3 else None,
        execution=execution,
    )
