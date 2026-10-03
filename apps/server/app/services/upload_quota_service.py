"""Serialize upload accounting until the caller commits or rolls back."""

from sqlalchemy import func, select

from app.core.config import settings
from app.core.media_quota import lock_workspace, workspace_media_limit, reserved_media_bytes, pending_cleanup_bytes
from app.core.workspace_context import required_workspace
from app.models import MediaFile


async def remaining_upload_bytes(session) -> int | None:
    if settings.runtime_execution_location != "cloud":
        return None
    workspace_id = required_workspace().workspace_id
    await session.run_sync(lock_workspace, workspace_id)
    total = await session.scalar(select(func.coalesce(func.sum(MediaFile.size), 0)).where(
        MediaFile.workspace_id == workspace_id))
    limit = await session.run_sync(workspace_media_limit, workspace_id)
    held = await session.run_sync(reserved_media_bytes, workspace_id)
    pending = await session.run_sync(pending_cleanup_bytes, workspace_id)
    return max(0, limit - int(total) - held - pending)
