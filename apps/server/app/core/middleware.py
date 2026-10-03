"""鉴权与请求上下文中间件。"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.database import SessionLocal
from app.core.errors import (
    AppError,
    InvalidTokenError,
    MalformedTokenError,
    MissingTokenError,
    UserUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import decode_login_token
from app.services import user_service

logger = get_logger(__name__)

# 无需登录即可访问的接口，其余 /api/* 全部要求有效 JWT
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/client/capabilities",
    }
)

# 文档类路径，仅开发环境暴露
DOC_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求分配 request_id 并记录耗时。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s (%.1fms) rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """默认拦截所有 /api/* 请求。

    新增路由自动受保护，除非显式加入 PUBLIC_PATHS。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # 非 API 路径与预检请求直接放过
        if not path.startswith("/api/") or request.method == "OPTIONS":
            return await call_next(request)

        if path in PUBLIC_PATHS or path == "/api/media/playback":
            return await call_next(request)

        user = None
        workspace_token = None
        try:
            user = await self._authenticate(request)
            from app.core.config import settings
            from app.core.errors import PermissionDeniedError
            if settings.runtime_execution_location == "cloud" and path.startswith("/api/workspaces"):
                raise PermissionDeniedError("云端内测仅支持个人空间，团队邀请与工作区切换未开放")
            from app.core.workspace_context import isolation_enabled, current_workspace, WorkspaceContext
            platform_path = path.startswith(("/api/auth/", "/api/workspaces", "/api/users", "/api/admin/", "/api/execution-settings")) or (path.startswith("/api/storage") and not path.startswith("/api/storage/media/"))
            if isolation_enabled() and not platform_path:
                from sqlalchemy import select
                from app.models.workspace import Workspace
                from app.services.workspace_service import require_membership
                async with SessionLocal() as session:
                    workspace_id = request.headers.get("X-Workspace-ID")
                    if not workspace_id or settings.runtime_execution_location == "cloud":
                        requested = workspace_id
                        workspace_id = await session.scalar(select(Workspace.id).where(Workspace.personal_user_id == user.id))
                        if settings.runtime_execution_location == "cloud" and requested and requested != workspace_id:
                            raise PermissionDeniedError("云端内测仅允许访问本人个人空间")
                    member = await require_membership(session, workspace_id or "", user.id)
                    workspace_token = current_workspace.set(WorkspaceContext(member.workspace_id, user.id, member.role))
            from app.services.permission_service import enforce_request
            enforce_request(user, request.url.path, request.method)
        except AppError as exc:
            logger.warning(
                "访问拒绝 code=%s path=%s rid=%s user_id=%s",
                exc.code,
                request.url.path,
                getattr(request.state, "request_id", None),
                getattr(request.state, "auth_user_id", None),
            )
            if user is not None:
                await self._audit(user.id, request, exc.status_code)
            if workspace_token is not None:
                current_workspace.reset(workspace_token)
            return self._error_response(request, exc)

        request.state.user = user
        try:
            response = await call_next(request)
            await self._audit(user.id, request, response.status_code)
            return response
        finally:
            if workspace_token is not None:
                current_workspace.reset(workspace_token)

    async def _audit(self, actor_id, request, status_code):
        if request.method not in {"GET", "HEAD", "OPTIONS"} or status_code >= 400:
            from app.models.audit import AuditEvent
            try:
                async with SessionLocal() as audit_session:
                    audit_session.add(AuditEvent(actor_id=actor_id,method=request.method,path=request.url.path[:512],status_code=status_code))
                    await audit_session.commit()
            except Exception:
                logger.error("操作审计写入失败 actor_id=%s", actor_id)

    async def _authenticate(self, request: Request):
        from app.services import browser_session_service
        if browser_session_service.cookie_auth_enabled():
            async with SessionLocal() as session:
                user = await browser_session_service.authenticate(session, request)
                request.state.auth_user_id = user.id
                session.expunge(user)
                return user
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise MissingTokenError()

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise MalformedTokenError()

        payload = decode_login_token(token)
        subject = payload.get("sub")
        if not subject:
            raise InvalidTokenError()
        try:
            request.state.auth_user_id = int(subject)
        except (TypeError, ValueError) as exc:
            raise InvalidTokenError() from exc

        async with SessionLocal() as session:
            user = await user_service.get_by_id(session, request.state.auth_user_id)
            if user is None:
                raise UserUnavailableError()
            if not user.is_active:
                raise UserUnavailableError()
            user_service.validate_session(user, payload, request.url.path)
            # 会话即将关闭，先断开与 session 的绑定，避免后续访问触发懒加载
            session.expunge(user)
            return user

    @staticmethod
    def _error_response(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": getattr(request.state, "request_id", None),
                "details": exc.details,
            },
        )
