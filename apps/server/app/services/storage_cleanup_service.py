"""Local-only cleanup. Never call user object storage deletion APIs."""

import asyncio
import time
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError
from app.core.workspace_context import system_scope
from app.models import Job, MediaFile, Project, User
from app.models.base import utcnow
from app.models.storage_cleanup import MediaCleanup


def local_path(relative):
    root = settings.storage_path.resolve()
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root) or path == root:
        raise ConflictError("Invalid managed cleanup path")
    # Do not follow a symlink/reparse point into another user's directory.
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ConflictError("Cleanup path contains a link")
    return path


async def drain_media(limit=100):
    with system_scope():
        async with SessionLocal() as session:
            rows = (await session.scalars(select(MediaCleanup).order_by(MediaCleanup.updated_at, MediaCleanup.id).limit(limit).with_for_update(skip_locked=True))).all()
            removed = 0
            for row in rows:
                from app.services.builtin_style_media_service import protected_path
                if protected_path(row.file_path):
                    await session.delete(row)
                    continue
                # A physically shared file remains charged until its last reference is gone.
                if await session.scalar(select(MediaFile.id).where(MediaFile.file_path == row.file_path).limit(1)):
                    row.updated_at = utcnow()
                    continue
                try:
                    await asyncio.to_thread(local_path(row.file_path).unlink, missing_ok=True)
                except (OSError, ConflictError):
                    row.updated_at = utcnow()
                    continue
                await session.delete(row)
                removed += 1
            await session.commit()
            return removed


async def supervise():
    last_audit = 0
    while True:
        if settings.runtime_execution_location == "cloud":
            from app.services.account_deletion_service import drain_accounts
            try:
                await drain_media()
                await drain_accounts()
                if time.monotonic() - last_audit >= 300:
                    await audit_accounts()
                    last_audit = time.monotonic()
            except Exception:
                from app.core.logging import get_logger
                get_logger(__name__).exception("Local storage cleanup deferred")
        await asyncio.sleep(5)


_audit_cursor = 0


async def audit_accounts():
    global _audit_cursor
    from app.models.workspace import Workspace
    with system_scope():
        async with SessionLocal() as session:
            rows = (await session.execute(select(Workspace.id, Workspace.personal_user_id).join(User, User.id == Workspace.personal_user_id).where(
                Workspace.personal_user_id > _audit_cursor, User.is_active.is_(True)).order_by(Workspace.personal_user_id).limit(2))).all()
        if not rows:
            _audit_cursor = 0
        for workspace, owner in rows:
            await audit_workspace(workspace, owner)
            _audit_cursor = owner


async def audit_workspace(workspace_id, owner_id):
    """Report mismatches and reclaim only aged, unreferenced private output files."""
    from app.core.media_quota import lock_workspace
    with system_scope():
        async with SessionLocal() as session:
            await session.run_sync(lock_workspace, workspace_id)
            # Active jobs may have unpublished files. Never sweep those directories.
            active = await session.scalar(select(Job.id).where(Job.workspace_id == workspace_id,
                Job.status.not_in(["succeeded", "failed", "cancelled"])).limit(1))
            media = (await session.scalars(select(MediaFile).where(MediaFile.workspace_id == workspace_id))).all()
            known = {row.file_path for row in media}
            known.update(await session.scalars(select(MediaCleanup.file_path).where(MediaCleanup.workspace_id == workspace_id)))
            projects = list(await session.scalars(select(Project.id).where(Project.workspace_id == workspace_id)))
            roots = [f"users/{owner_id}", *[f"projects/{ident}" for ident in projects]]

            def scan():
                issues, orphans = [], []
                for row in media:
                    target = local_path(row.file_path)
                    if not target.is_file():
                        issues.append({"media_id": row.id, "reason": "missing_file"})
                    elif target.stat().st_size != row.size:
                        issues.append({"media_id": row.id, "reason": "size_mismatch"})
                if active:
                    return issues, orphans
                cutoff = time.time() - 24 * 3600
                for relative in roots:
                    root = local_path(relative)
                    if not root.exists():
                        continue
                    for target in root.rglob("*"):
                        path = target.relative_to(settings.storage_path).as_posix()
                        local_path(path)
                        if target.is_file() and path not in known and target.stat().st_mtime < cutoff:
                            # Upload locking files and internal cache are not user materials.
                            if target.name.endswith(".lock"):
                                continue
                            orphans.append((path, target.stat().st_size))
                            if len(orphans) >= 100:
                                return issues, orphans
                return issues, orphans

            issues, orphans = await asyncio.to_thread(scan)
            for path, size in orphans:
                session.add(MediaCleanup(workspace_id=workspace_id, owner_id=owner_id, file_path=path, size=size))
            await session.commit()
            if issues:
                from app.core.logging import get_logger
                get_logger(__name__).warning("Media reconciliation workspace=%s issues=%s", workspace_id, issues[:20])
            return {"issues": issues, "queued_cleanup": len(orphans), "active_jobs_skipped": bool(active)}
