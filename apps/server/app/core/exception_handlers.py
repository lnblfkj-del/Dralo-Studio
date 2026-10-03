"""统一异常处理器。

所有异常在此收口，保证前端永远拿到 {code, message, request_id, details} 结构。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _payload(
    request: Request, code: str, message: str, details: dict | None = None
) -> dict:
    return {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
        "details": details or {},
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        from app.core.job_failure import local_exception
        code, message, details = local_exception(exc)
        logger.exception("数据库操作失败 rid=%s", getattr(request.state, "request_id", None), exc_info=exc)
        return JSONResponse(status_code=503 if code == "LOCAL_DATABASE_TRANSIENT" else 500,
                            content=_payload(request, code, message, details))

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "业务异常 %s: %s (%s %s)",
            exc.code,
            exc.message,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(request, exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """把 Pydantic 报错转成可读中文提示。"""
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ())[1:])
        message = f"参数 {location} 不合法" if location else "请求参数不合法"
        return JSONResponse(
            status_code=422,
            content=_payload(
                request,
                "VALIDATION_ERROR",
                message,
                {"errors": [
                    {"loc": list(err.get("loc", ())), "msg": err.get("msg", "")}
                    for err in exc.errors()
                ]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = "请求的资源不存在" if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code, content=_payload(request, code, message)
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """兜底：记录完整堆栈，但不把内部细节返回给前端。"""
        logger.exception(
            "未捕获异常 (%s %s)", request.method, request.url.path, exc_info=exc
        )
        return JSONResponse(
            status_code=500,
            content=_payload(
                request, "INTERNAL_ERROR", "服务内部错误，请稍后重试"
            ),
        )
