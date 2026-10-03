"""Default-deny ORM boundary; services still validate their business contracts."""

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import TextClause

from app.core.errors import PermissionDeniedError
from app.core.workspace_context import _system_access, isolation_enabled, required_workspace
from app.models.workspace_scoped import WorkspaceScoped


def _scoped_tables():
    from app.core.database import Base
    return {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers
            if issubclass(mapper.class_, WorkspaceScoped)}


def _pin(session, workspace_id):
    previous = session.info.setdefault("workspace_id", workspace_id)
    if previous != workspace_id:
        raise PermissionDeniedError("数据库会话不能跨工作空间复用")


def _require_live_workspace(session, workspace_id):
    from app.core.config import settings
    if settings.runtime_execution_location != "cloud":
        return
    from app.core.media_quota import lock_workspace
    from app.models.user import User
    from app.models.workspace import Workspace
    lock_workspace(session, workspace_id)
    active = session.scalar(select(User.is_active).join(Workspace, Workspace.personal_user_id == User.id).where(Workspace.id == workspace_id))
    if active is not True:
        raise PermissionDeniedError("个人账号已停用或正在删除，拒绝写入迟到结果")


@event.listens_for(Session, "do_orm_execute")
def scoped_statements(state):
    if not isolation_enabled() or _system_access.get():
        return
    if isinstance(state.statement, TextClause):
        raise PermissionDeniedError("空间会话不允许执行原始 SQL")
    tables = _scoped_tables()
    touched = any(getattr(node, "name", None) in tables for node in visitors.iterate(state.statement))
    if not touched:
        return
    context = required_workspace()
    _pin(state.session, context.workspace_id)
    if not state.is_orm_statement or state.is_insert:
        raise PermissionDeniedError("空间资源必须通过受控 ORM 访问")
    if state.is_update or state.is_delete:
        _require_live_workspace(state.session, context.workspace_id)
        if context.role == "viewer":
            raise PermissionDeniedError("只读成员不能修改空间资源")
        if state.parameters:
            # Executemany/bind parameters bypass the literal FK validation below.
            raise PermissionDeniedError("空间批量写入必须使用显式字段值")
        values = getattr(state.statement, "_values", {}) or {}
        for key, value in values.items():
            name = getattr(key, "key", key)
            if name == "workspace_id":
                raise PermissionDeniedError("不能修改资源的工作空间归属")
            column = state.statement.table.c.get(name)
            if column is not None:
                for fk in column.foreign_keys:
                    parent = tables.get(fk.column.table.name)
                    if parent is not None:
                        ident = getattr(value, "value", None)
                        if ident is None:
                            if getattr(value, "__visit_name__", None) != "null" and not hasattr(value, "value"):
                                raise PermissionDeniedError("不支持此关联字段更新方式")
                        elif state.session.scalar(select(parent.id).where(parent.id == ident)) is None:
                            raise PermissionDeniedError("关联资源不存在或属于其他空间")
    workspace_id = context.workspace_id
    state.statement = state.statement.options(with_loader_criteria(
        WorkspaceScoped, lambda cls: cls.workspace_id == workspace_id, include_aliases=True
    ))


@event.listens_for(Session, "before_flush")
def scoped_writes(session, flush_context, instances):
    if not isolation_enabled() or _system_access.get():
        return
    objects = [obj for obj in session.new | session.dirty | session.deleted
               if isinstance(obj, WorkspaceScoped)]
    if not objects:
        return
    # Track output before permission validation can reject a late publication.
    from app.core.config import settings
    from app.models.asset import MediaFile
    from app.services.source_file_transaction import track
    for obj in session.new if settings.runtime_execution_location == "cloud" else ():
        if isinstance(obj, MediaFile) and obj.file_path:
            existing = session.scalar(select(MediaFile.id).where(MediaFile.file_path == obj.file_path).limit(1))
            if existing is None:
                track(session, settings.storage_path, obj.file_path)
    context = required_workspace()
    _pin(session, context.workspace_id)
    if context.role == "viewer":
        raise PermissionDeniedError("只读成员不能修改空间资源")
    _require_live_workspace(session, context.workspace_id)
    tables = _scoped_tables()
    for obj in objects:
        if obj in session.new and obj.workspace_id is None:
            obj.workspace_id = context.workspace_id
        if obj in session.new and obj.__tablename__ == "jobs":
            obj.requested_by = context.actor_id
        if obj.workspace_id != context.workspace_id:
            raise PermissionDeniedError("不能写入其他空间资源")
        if obj not in session.new and inspect(obj).attrs.workspace_id.history.has_changes():
            raise PermissionDeniedError("不能修改资源的工作空间归属")
        for column in inspect(type(obj)).columns:
            if column.key == "workspace_id":
                continue
            ident = getattr(obj, column.key)
            if ident is None:
                continue
            for fk in column.foreign_keys:
                parent = tables.get(fk.column.table.name)
                if parent is None:
                    continue
                related = session.get(parent, ident)
                if related is None or related.workspace_id != context.workspace_id:
                    raise PermissionDeniedError("关联资源不存在或属于其他空间")
