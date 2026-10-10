"""FastAPI 应用入口。

启动方式见 scripts/dev-server.sh，生产由 systemd 管理 uvicorn。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    agent_config,
    assets,
    auth,
    canvas,
    creation,
    execution,
    health,
    jobs,
    market_research,
    media,
    projects,
    providers,
    script_versions,
    storage,
    ui_diagnostics,
    workspaces,
)
from app.core.config import PROJECT_ROOT, settings
from app.core.database import SessionLocal, dispose_engine
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import AuthMiddleware, RequestContextMiddleware
from app.frontend import register_frontend, select_frontend_dist
from app.editions import register_edition_routes
from app.services import agent_config_service, business_executor_service, execution_policy_service, user_service

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭钩子。"""
    from app.core.production_safety import validate_production_settings
    validate_production_settings(settings)
    from app.core.storage_safety import validate_storage_startup
    validate_storage_startup(settings)
    setup_logging()
    from app.core.runtime_environment import windows_token_restricted
    restricted = windows_token_restricted()
    logger.info("Backend runtime restricted_token=%s", restricted)
    if restricted:
        logger.warning("后端继承了受限的 Windows 进程令牌；外部渠道连接可能被运行环境阻止。请通过正常终端或获准联网的启动入口运行。")

    # 确保运行时目录存在
    for path in (settings.storage_path, settings.log_path):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("projects", "temp", "cache"):
        (settings.storage_path / name).mkdir(parents=True, exist_ok=True)

    # 空库时创建初始管理员，便于首次登录
    async def initialize():
        async with SessionLocal() as session:
            await user_service.ensure_bootstrap_admin(
                session,
                settings.bootstrap_admin_username,
                settings.bootstrap_admin_password,
            )
            from app.core.workspace_context import isolation_enabled
            if not isolation_enabled():
                await agent_config_service.ensure_default_styles(session)
                await business_executor_service.ensure_executor_settings(session)
            await execution_policy_service.load_runtime_policy(session)
            await session.commit()
    from app.services.background_leader_service import initialize_once_at_a_time
    await initialize_once_at_a_time(initialize)

    logger.info("%s 启动完成 (env=%s)", settings.app_name, settings.app_env)
    from app.services.canvas_workflow_service import supervise
    from app.services.background_leader_service import run_singleton
    workflow_supervisor = asyncio.create_task(run_singleton("canvas_workflow", supervise))
    from app.services.storage_service import mirror_loop
    storage_mirror = asyncio.create_task(run_singleton("storage_mirror", mirror_loop))
    from app.services.storage_cleanup_service import supervise as storage_cleanup
    cleanup_supervisor = asyncio.create_task(run_singleton("storage_cleanup", storage_cleanup))
    from app.services.audio_result_cleanup import supervise as audio_cleanup
    audio_cleanup_supervisor = asyncio.create_task(run_singleton("audio_result_cleanup", audio_cleanup))
    try:
        yield
    finally:
        audio_cleanup_supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await audio_cleanup_supervisor
        cleanup_supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_supervisor
        storage_mirror.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await storage_mirror
        workflow_supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await workflow_supervisor
        await dispose_engine()
    logger.info("服务已关闭")


def create_app() -> FastAPI:
    # 生产环境不暴露交互式文档
    docs_url = None if settings.is_production else "/docs"

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # 中间件为后进先出：先注册的最后执行，
    # 因此 RequestContext 放在 Auth 之后注册，保证鉴权失败时也带上 request_id
    from app.core.request_admission import RequestAdmissionMiddleware
    app.add_middleware(RequestAdmissionMiddleware)
    app.add_middleware(AuthMiddleware)
    from app.core.request_limits import RequestSizeMiddleware
    app.add_middleware(RequestSizeMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(ui_diagnostics.router)
    api.include_router(auth.router)
    api.include_router(workspaces.router)
    from app.api import users
    api.include_router(users.router)
    register_edition_routes(api, execution_location=settings.runtime_execution_location)
    api.include_router(projects.router)
    api.include_router(assets.library_router)
    api.include_router(assets.router)
    api.include_router(media.router)
    api.include_router(market_research.router)
    api.include_router(canvas.router)
    api.include_router(creation.router)
    api.include_router(execution.router)
    api.include_router(script_versions.router)
    api.include_router(providers.router)
    from app.api import style_categories
    api.include_router(style_categories.router)
    api.include_router(agent_config.router)
    api.include_router(jobs.router)
    api.include_router(storage.router)
    app.include_router(api)

    register_frontend(app, select_frontend_dist(PROJECT_ROOT / "apps/web", execution_location=settings.runtime_execution_location), execution_location=settings.runtime_execution_location)

    return app


app = create_app()
