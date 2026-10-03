"""Trusted request/job context, never populated from an unchecked client header."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.config import settings
from app.core.errors import PermissionDeniedError


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: str
    actor_id: int
    role: str


current_workspace: ContextVar[WorkspaceContext | None] = ContextVar("workspace", default=None)
_system_access: ContextVar[bool] = ContextVar("workspace_system_access", default=False)


def isolation_enabled() -> bool:
    return settings.runtime_execution_location == "cloud" or settings.workspace_isolation_enabled


def required_workspace() -> WorkspaceContext:
    context = current_workspace.get()
    if context is None:
        raise PermissionDeniedError("缺少已验证的工作空间上下文")
    return context


@contextmanager
def workspace_scope(workspace_id: str, actor_id: int, role: str):
    token = current_workspace.set(WorkspaceContext(workspace_id, actor_id, role))
    system_token = _system_access.set(False)
    try:
        yield
    finally:
        _system_access.reset(system_token)
        current_workspace.reset(token)


@contextmanager
def system_scope():
    """Internal scheduler/bootstrap only. Never exposed through HTTP parameters."""
    token = _system_access.set(True)
    try:
        yield
    finally:
        _system_access.reset(token)
