"""Internal-only verified export inputs for an isolated edit draft."""

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import MediaFile
from app.services.episode_edit_draft_service import get_verified_draft_with_sources
from app.services.episode_edit_timed_export_projection import project_timed_export_rows
from app.services.episode_edit_video_export_projection import project_video_export_rows


async def build_verified_edit_export_projection(
    session: AsyncSession, *, episode_id: int, owner_id: int,
) -> dict[str, Any]:
    """Compile all tracks from server-verified media; never submit an export job."""
    document, source_frames = await get_verified_draft_with_sources(
        session, episode_id=episode_id, owner_id=owner_id,
    )
    storage_root = settings.storage_path.resolve()
    paths_by_media = {}
    for media_id in source_frames:
        media = await session.get(MediaFile, media_id)
        if media is None:
            raise ConflictError("剪辑来源素材已被移除")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ConflictError("剪辑来源素材文件已变化")
        paths_by_media[media_id] = str(path)
    video = project_video_export_rows(
        document, source_frames=source_frames, paths_by_media=paths_by_media,
    )
    timed = project_timed_export_rows(
        document, source_frames=source_frames, paths_by_media=paths_by_media,
    )
    if video["duration_frames"] != timed["duration_frames"]:
        raise ConflictError("视频与声音时间线总帧数不一致")
    return {
        "episode_id": episode_id,
        "revision": document.revision,
        "source_fingerprint": document.source_fingerprint,
        "video": video,
        "timed": timed,
        "blockers_by_export": timed["blockers_by_export"],
    }
