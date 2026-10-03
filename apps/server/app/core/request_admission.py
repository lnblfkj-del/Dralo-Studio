"""Single-host HTTP admission shared by API processes using kernel file locks."""

from filelock import FileLock, Timeout
from starlette.responses import JSONResponse

from app.core.config import settings


def request_category(scope):
    path = scope["path"].rstrip("/")
    if scope.get("method") == "GET" and path.startswith("/api/jobs/") and path.endswith("/events"):
        return "event"
    if scope.get("method") in {"POST", "PUT", "PATCH"}:
        if path in {"/api/creation/reference", "/api/creation/reference-jobs"}:
            return "document"
        if path.startswith("/api/media/export/episodes/"):
            return "export"
        content_type = next((v.lower() for k, v in scope.get("headers", []) if k.lower() == b"content-type"), b"")
        if (content_type.startswith(b"multipart/form-data") or path == "/api/media/upload"
                or path.endswith("/versions/upload") or path.endswith("/director/previews")):
            return "upload"
    return "api"


def acquire_slot(category):
    if category not in {"api", "upload", "document", "export", "event"}:
        raise ValueError("Unknown admission category")
    if settings.storage_required_mount is not None and not settings.storage_required_mount.is_mount():
        raise OSError("Required data disk unavailable")
    root = settings.storage_config_path.parent / "request-slots"
    root.mkdir(exist_ok=True)
    # Lock files must remain in place even after release; unlinking introduces
    # different inodes that can be simultaneously locked by different workers.
    for number in range(getattr(settings, f"cloud_{category}_slots")):
        lock = FileLock(root / f"{category}-{number}.lock", thread_local=False)
        try:
            lock.acquire(timeout=0)
            return lock
        except Timeout:
            continue
    return None


class RequestAdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or settings.runtime_execution_location != "cloud"
                or not scope.get("path", "").startswith("/api/")
                or scope["path"].rstrip("/") == "/api/health"):
            return await self.app(scope, receive, send)
        category = request_category(scope)
        try:
            lock = acquire_slot(category)
        except OSError:
            response = JSONResponse(status_code=503, content={"code": "ADMISSION_UNAVAILABLE",
                "message": "服务端接收控制暂不可用，请稍后重试", "details": {},
                "request_id": scope.get("state", {}).get("request_id")})
            return await response(scope, receive, send)
        if lock is None:
            response = JSONResponse(status_code=429, headers={"Retry-After": "5"}, content={
                "code": "REQUEST_CAPACITY_REACHED", "message": "当前同类请求较多，请稍后重试",
                "details": {"category": category}, "request_id": scope.get("state", {}).get("request_id")})
            return await response(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            lock.release()
