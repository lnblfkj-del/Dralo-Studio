"""Server-enforced settings permissions; unknown settings mutations fail closed."""
from app.core.errors import PermissionDeniedError

PERMISSIONS = {
    "projects.view": "项目与素材：查看团队内容",
    "projects.create": "项目：新建",
    "projects.edit": "项目、画布、大纲、分集及素材：编辑",
    "projects.delete": "项目及素材：删除（项目删除不可恢复）",
    "tasks.generate": "任务：提交生成 / 执行（可能产生费用）",
    "tasks.retry": "任务：重试（可能产生费用）",
    "tasks.cancel": "任务：取消 / 暂停",
    "tasks.delete": "任务：移入回收站",
    "tasks.restore": "任务：从回收站恢复",
    "tasks.purge": "任务回收站：永久删除",
    "providers.edit": "模型渠道：新增、修改、密钥及测试",
    "providers.delete": "模型渠道：删除渠道或模型",
    "ai.edit": "Ai 设置：新增与修改",
    "ai.delete": "Ai 设置：删除",
    "styles.edit": "风格管理：新增与修改",
    "styles.delete": "风格管理：删除",
    "storage.edit": "存储：查看、修改位置、密钥及云同步",
    "execution.edit": "执行管理：修改并发、重试和超时策略",
}

def allowed(user, permission):
    from app.core.config import settings
    if permission == "storage.edit" and settings.runtime_execution_location == "cloud":
        return user.is_active
    from app.core.workspace_context import isolation_enabled, current_workspace
    if isolation_enabled():
        if permission in {"storage.edit", "execution.edit"}:
            return user.is_admin
        context = current_workspace.get()
        if context is None:
            return False
        if context.role in {"owner", "admin"}:
            return True
        if context.role == "viewer":
            return permission == "projects.view"
        return permission in {"projects.view", "projects.create", "projects.edit", "tasks.generate", "tasks.retry", "tasks.cancel"}
    return user.is_admin or bool((user.permissions or {}).get(permission, False))

def settings_permission(path, method):
    if path.startswith("/api/execution-settings"):
        return "execution.edit" if method not in {"GET", "HEAD"} else None
    if path.startswith("/api/providers/ai-settings"):
        return "ai.edit" if method not in {"GET", "HEAD"} else None
    if path.startswith("/api/providers"):
        if method in {"GET", "HEAD"} or path.endswith("/estimate"):
            return None
        return "providers.delete" if method=="DELETE" else "providers.edit"
    if path.startswith("/api/agent-config"):
        if method in {"GET", "HEAD"}:
            return None
        module="styles" if path.startswith("/api/agent-config/styles") else "ai"
        return module+(".delete" if method=="DELETE" else ".edit")
    if path.startswith("/api/storage") and not path.startswith("/api/storage/media/"):
        return "storage.edit"
    return None

def enforce_request(user, path, method):
    from app.core.workspace_context import isolation_enabled, current_workspace
    scoped_role = current_workspace.get().role if isolation_enabled() and current_workspace.get() else user.role
    for required in project_permissions(path, method):
        if not allowed(user, required):
            raise PermissionDeniedError("缺少权限：" + PERMISSIONS[required])
    if path.startswith("/api/jobs/") and path.endswith("/purge") and not allowed(user,"tasks.purge"):
        raise PermissionDeniedError("无永久删除任务权限")
    permission=settings_permission(path,method)
    if permission and not allowed(user,permission):
        raise PermissionDeniedError("无权限执行此设置操作，请联系管理员授权")
    read_action = method=="POST" and (path.endswith("/playback-url") or path.endswith("/estimate"))
    if scoped_role=="viewer" and method not in {"GET","HEAD","OPTIONS"} and path not in {"/api/auth/profile", "/api/auth/change-password", "/api/auth/refresh", "/api/auth/logout-all", "/api/auth/logout", "/api/workspaces/invitations/accept"} and not read_action:
        raise PermissionDeniedError("只读账号不能执行写入或生成操作")


def project_permissions(path, method):
    """Conservative gate for all business APIs, including nested canvas actions.

    POST business actions may enqueue work: require generation authorization unless
    explicitly identified as a read-only or lifecycle operation.
    """
    business = ("/api/projects", "/api/assets", "/api/media", "/api/jobs", "/api/creation", "/api/market-research")
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in business):
        return []
    if path == "/api/projects" and method == "POST":
        return ["projects.create"]
    if method == "POST":
        import re
        if re.fullmatch(r"/api/creation/sessions/\d+/artifacts/\d+/(story-bible/version|confirm)", path) or re.fullmatch(r"/api/projects/\d+/creation-session/artifacts/\d+/restore", path):
            return ["projects.view", "projects.edit"]
    if path.startswith("/api/creation/import-sessions") and method == "POST":
        # Import analysis and confirmation are local, transactional writes. They
        # never enqueue a billable model job, so tasks.generate must not gate them.
        return ["projects.view", "projects.create", "projects.edit"]
    if method == "POST" and (path.startswith("/api/projects/from-") or path.startswith("/api/creation/")):
        return ["projects.view", "projects.create", "projects.edit", "tasks.generate"]
    required = ["projects.view"]
    if method in {"GET", "HEAD", "OPTIONS"} or path.endswith(("/playback-url", "/estimate")):
        return required
    if path.startswith("/api/jobs"):
        if path.endswith("/bulk-actions"):
            return required  # payload action is checked in the route
        action = path.rsplit("/",1)[-1]
        permission = {"retry":"tasks.retry", "reprocess-response":"tasks.retry", "cancel":"tasks.cancel", "pause":"tasks.cancel", "resume":"tasks.generate", "restore":"tasks.restore", "purge":"tasks.purge"}.get(action)
        return required + [permission or ("tasks.delete" if method=="DELETE" else "tasks.generate")]
    required.append("projects.delete" if method=="DELETE" else "projects.edit")
    if method=="POST" and not path.endswith(("/upload", "/link", "/final")):
        required.append("tasks.generate")
    return required
