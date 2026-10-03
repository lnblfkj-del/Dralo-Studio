"""Streaming request caps before multipart spooling, without buffering bodies."""

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

from app.core.config import settings


class BodyTooLarge(MultiPartException):
    """Multipart parsers close already-created temporary files on this error."""


class RequestSizeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or settings.runtime_execution_location != "cloud"
                or not scope.get("path", "").startswith("/api/")):
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        content_type = next((v.lower() for k, v in headers if k.lower() == b"content-type"), b"")
        limit = settings.request_json_max_bytes
        if content_type.startswith(b"multipart/form-data"):
            limit = settings.request_upload_max_bytes
        if scope["path"].rstrip("/") in {"/api/creation/reference", "/api/creation/reference-jobs"}:
            limit = min(limit, settings.request_document_max_bytes)

        def response(status=413):
            return JSONResponse(status_code=status, content={
                "code": "REQUEST_TOO_LARGE" if status == 413 else "INVALID_CONTENT_LENGTH",
                "message": "请求内容超过服务端接收上限，请缩小文件或拆分请求" if status == 413 else "请求长度格式无效",
                "request_id": scope.get("state", {}).get("request_id"),
                "details": {"max_bytes": limit},
            })

        lengths = [value for key, value in headers if key.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await response(400)(scope, receive, send)
            # Bound digit parsing as well as byte size.
            if len(lengths[0]) > 20 or int(lengths[0]) > limit:
                return await response()(scope, receive, send)
        received = 0
        exceeded = False
        replaced = False

        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise BodyTooLarge("request body exceeds server limit")
            return message

        async def bounded_send(message):
            nonlocal replaced
            if exceeded:
                if not replaced:
                    replaced = True
                    await response()(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except BodyTooLarge:
            if not replaced:
                await response()(scope, receive, send)
