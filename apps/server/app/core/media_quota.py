"""Transaction-serialized quota for persisted media, independent of its source."""

from collections import defaultdict

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ConflictError
from app.services.builtin_style_media_service import PURPOSE, protected_path, validate


def workspace_media_limit(session, workspace_id):
    from app.models.user import User
    from app.models.workspace import Workspace
    limit = session.scalar(select(User.media_quota_bytes).join(
        Workspace, Workspace.personal_user_id == User.id).where(Workspace.id == workspace_id))
    return settings.workspace_media_quota_bytes if limit is None else int(limit)


def reserved_media_bytes(session, workspace_id):
    from app.models.job import Job
    from app.models.media_reservation import MediaReservation
    query = select(func.coalesce(func.sum(MediaReservation.size), 0)).join(
        Job, Job.id == MediaReservation.job_id).where(
        MediaReservation.workspace_id == workspace_id,
        Job.status.not_in(["succeeded", "failed", "cancelled"]))
    consuming = session.info.get("media_quota_consuming_job")
    if consuming is not None:
        query = query.where(MediaReservation.job_id != consuming)
    return int(session.scalar(query) or 0)


def pending_cleanup_bytes(session, workspace_id):
    from app.models.storage_cleanup import MediaCleanup
    return int(session.scalar(select(func.coalesce(func.sum(MediaCleanup.size), 0)).where(
        MediaCleanup.workspace_id == workspace_id)) or 0)


async def require_media_budget(session, size):
    if settings.runtime_execution_location != "cloud":
        return
    from app.core.workspace_context import required_workspace
    from app.models.asset import MediaFile
    workspace_id = required_workspace().workspace_id
    await session.run_sync(lock_workspace, workspace_id)
    used = int(await session.scalar(select(func.coalesce(func.sum(MediaFile.size), 0)).where(
        MediaFile.workspace_id == workspace_id, MediaFile.purpose != PURPOSE)) or 0)
    limit = await session.run_sync(workspace_media_limit, workspace_id)
    held = await session.run_sync(reserved_media_bytes, workspace_id)
    pending = await session.run_sync(pending_cleanup_bytes, workspace_id)
    if size < 0 or used + pending + held + size > limit:
        raise ConflictError("工作空间媒体总额度不足，请清理素材或联系管理员调整额度；未写入本次文件")


def lock_workspace(session, workspace_id):
    from app.models.workspace import Workspace
    try:
        if session.bind.dialect.name == "postgresql":
            session.execute(select(func.set_config("lock_timeout", "5000ms", True)))
            found = session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
        else:
            found = session.execute(update(Workspace).where(Workspace.id == workspace_id).values(
                name=Workspace.name, updated_at=Workspace.updated_at)).rowcount == 1
    except OperationalError as exc:
        raise ConflictError("空间额度核算繁忙，请稍后重试") from exc
    if not found:
        raise ConflictError("工作空间不可用，不能保存素材")


@event.listens_for(Session, "do_orm_execute")
def prevent_quota_bypass(state):
    if settings.runtime_execution_location != "cloud":
        return
    statement = state.statement
    if getattr(getattr(statement, "table", None), "name", None) != "media_files":
        return
    values = getattr(statement, "_values", {}) or {}
    fields = {getattr(key, "key", key) for key in values}
    if state.is_delete and not state.session.info.get("account_purge"):
        raise ConflictError("云端媒体删除必须逐条登记文件清理，不能直接批量删除记录")
    if state.is_insert or (state.is_update and (state.parameters or fields & {"size", "workspace_id", "purpose", "file_path", "hash", "source"})):
        raise ConflictError("媒体大小和空间归属必须通过逐条入库额度检查，不能批量绕过")


@event.listens_for(Session, "before_flush")
def enforce_media_quota(session, flush_context, instances):
    if settings.runtime_execution_location != "cloud":
        return
    from app.models.asset import MediaFile
    groups = defaultdict(list)
    for item in session.new | session.dirty | session.deleted:
        if isinstance(item, MediaFile):
            if item.purpose == PURPOSE or protected_path(item.file_path):
                validate(item)
                continue
            if item.workspace_id is None:
                raise ConflictError("素材缺少工作空间归属")
            groups[item.workspace_id].append(item)
    # Stable lock order also protects trusted multi-workspace maintenance.
    for workspace_id in sorted(groups):
        lock_workspace(session, workspace_id)
        used = int(session.scalar(select(func.coalesce(func.sum(MediaFile.size), 0)).where(
            MediaFile.workspace_id == workspace_id, MediaFile.purpose != PURPOSE)) or 0)
        delta = 0
        for item in groups[workspace_id]:
            old_size = 0 if item in session.new else int(session.scalar(select(MediaFile.size).where(
                MediaFile.id == item.id, MediaFile.workspace_id == workspace_id)) or 0)
            new_size = 0 if item in session.deleted else int(item.size or 0)
            if new_size < 0:
                raise ConflictError("素材大小不能为负数")
            delta += new_size - old_size
            if item in session.new:
                from app.services.source_file_transaction import track
                existing = session.scalar(select(MediaFile.id).where(MediaFile.file_path == item.file_path).limit(1))
                if existing is None:
                    track(session, settings.storage_path, item.file_path)
            if item in session.deleted:
                from app.models.storage_cleanup import MediaCleanup
                session.add(MediaCleanup(workspace_id=workspace_id, owner_id=item.owner_id,
                                         file_path=item.file_path, size=old_size))
                # File removal has not happened yet: deleting its row cannot grant space.
                delta += old_size
        if delta > 0 and used + pending_cleanup_bytes(session, workspace_id) + reserved_media_bytes(session, workspace_id) + delta > workspace_media_limit(session, workspace_id):
            raise ConflictError("工作空间媒体总额度不足，请清理素材或联系管理员调整额度；本次结果未入库")
