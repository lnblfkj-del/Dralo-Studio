"""Media catalog, upload validation, ownership and project links."""

import asyncio
import contextlib
import json
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.models import AssetVersion, MediaFile, Project, ProjectAssetLink, ProjectMediaLink
from app.services.team_access import owner_scope, same_team

MEDIA_RULES = {
    "image": {
        "mime": {"image/png", "image/jpeg", "image/webp", "image/gif"},
        "extensions": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
        "max_bytes": 20 * 1024 * 1024,
    },
    "video": {
        "mime": {"video/mp4", "video/webm", "video/quicktime"},
        "extensions": {".mp4", ".webm", ".mov"},
        "max_bytes": 500 * 1024 * 1024,
    },
    "audio": {
        "mime": {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg"},
        "extensions": {".mp3", ".wav", ".m4a", ".ogg"},
        "max_bytes": 100 * 1024 * 1024,
    },
    "file": {
        "mime": {
            "text/plain", "text/markdown", "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        "extensions": {".txt", ".md", ".pdf", ".docx"},
        "max_bytes": 20 * 1024 * 1024,
    },
    "model": {
        "mime": {
            "application/octet-stream", "model/gltf-binary", "model/gltf+json",
            "model/fbx", "model/obj", "text/plain", "application/json",
        },
        "extensions": {".fbx", ".obj", ".glb", ".gltf"},
        "max_bytes": 250 * 1024 * 1024,
    },
}


def classify_upload(filename: str, content_type: str | None) -> tuple[str, int, str]:
    safe_name = Path(filename).name
    extension = Path(safe_name).suffix.lower()
    mime_type = (content_type or "").lower().split(";", 1)[0]
    for kind, rule in MEDIA_RULES.items():
        if extension in rule["extensions"] and mime_type in rule["mime"]:
            return kind, int(rule["max_bytes"]), safe_name
    raise ConflictError("不支持的媒体格式或文件类型与扩展名不匹配")


async def probe_media_file(path: Path, kind: str) -> dict[str, int | float | None]:
    """使用 ffprobe 提取图片、视频与音频的基础信息，失败时保持上传可用。"""
    if kind not in {"image", "video", "audio"}:
        return {"width": None, "height": None, "duration": None}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            settings.ffprobe_path, "-v", "error", "-protocol_whitelist", "file", "-max_alloc", "268435456", "-show_streams", "-show_format",
            "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        if process.returncode != 0:
            return {"width": None, "height": None, "duration": None}
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        streams = data.get("streams") or []
        visual = next((item for item in streams if item.get("codec_type") == "video"), {})
        duration_value = visual.get("duration") or (data.get("format") or {}).get("duration")
        return {
            "width": int(visual["width"]) if visual.get("width") else None,
            "height": int(visual["height"]) if visual.get("height") else None,
            "duration": float(duration_value) if duration_value is not None else None,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError, asyncio.TimeoutError):
        return {"width": None, "height": None, "duration": None}
    finally:
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()


async def ensure_media_metadata(session: AsyncSession, media: MediaFile) -> MediaFile:
    needs_visual = media.kind in {"image", "video"} and (not media.width or not media.height)
    needs_duration = media.kind in {"video", "audio"} and media.duration is None
    if not needs_visual and not needs_duration:
        return media
    storage_root = settings.storage_path.resolve()
    path = (storage_root / Path(media.file_path)).resolve()
    if not path.is_relative_to(storage_root) or not path.is_file():
        return media
    metadata = await probe_media_file(path, media.kind)
    media.width = metadata["width"] or media.width
    media.height = metadata["height"] or media.height
    media.duration = metadata["duration"] if metadata["duration"] is not None else media.duration
    await session.flush()
    return media


def validate_decodable_media(media: MediaFile, expected_kind: str) -> None:
    """Reject uploads whose extension/MIME match but payload cannot be decoded."""
    if media.kind != expected_kind:
        raise ConflictError("上传媒体类型与资产类型不匹配")
    if expected_kind == "image" and (not media.width or not media.height):
        raise ConflictError("图片内容无法识别或文件已损坏")
    if expected_kind == "video" and (
        not media.width or not media.height or media.duration is None or media.duration <= 0
    ):
        raise ConflictError("视频内容无法识别或文件已损坏")
    if expected_kind == "audio" and media.duration is None:
        raise ConflictError("音频内容无法识别或文件已损坏")


async def list_media(
    session: AsyncSession,
    owner_id: int,
    *,
    limit: int,
    offset: int,
    kind: str | None,
    source: str | None,
    keyword: str | None,
    project_id: int | None,
    global_only: bool = False,
) -> tuple[list[MediaFile], int]:
    conditions = [owner_scope(MediaFile.owner_id, owner_id), MediaFile.purpose.not_in(["style", "builtin_style"])]
    if kind:
        conditions.append(MediaFile.kind == kind)
    if source:
        conditions.append(MediaFile.source == source)
    if project_id is not None:
        conditions.append(MediaFile.project_id == project_id)
    elif global_only:
        conditions.append(MediaFile.project_id.is_(None))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append(or_(MediaFile.original_name.like(pattern), MediaFile.hash.like(pattern)))
    statement = (
        select(MediaFile).where(*conditions)
        .order_by(MediaFile.created_at.desc(), MediaFile.id.desc())
        .limit(limit).offset(offset)
    )
    count_statement = select(func.count()).select_from(MediaFile).where(*conditions)
    return (
        list((await session.execute(statement)).scalars()),
        int(await session.scalar(count_statement) or 0),
    )


async def to_media_out(session: AsyncSession, media: MediaFile) -> dict:
    direct_project_ids = list((await session.execute(
        select(ProjectMediaLink.project_id).where(
            ProjectMediaLink.media_file_id == media.id
        ).order_by(ProjectMediaLink.project_id)
    )).scalars())
    asset_project_ids = list((await session.execute(
        select(ProjectAssetLink.project_id)
        .join(AssetVersion, AssetVersion.asset_id == ProjectAssetLink.asset_id)
        .where(AssetVersion.media_file_id == media.id)
        .order_by(ProjectAssetLink.project_id)
    )).scalars())
    project_ids = sorted(set(direct_project_ids) | set(asset_project_ids))
    return {
        "id": media.id,
        "project_id": media.project_id,
        "owner_id": media.owner_id,
        "kind": media.kind,
        "source": media.source,
        "original_name": media.original_name,
        "mime_type": media.mime_type,
        "size": media.size,
        "width": media.width,
        "height": media.height,
        "duration": media.duration,
        "hash": media.hash,
        "linked_project_ids": project_ids,
        "created_at": media.created_at,
        "updated_at": media.updated_at,
    }


async def get_owned_media(
    session: AsyncSession, media_id: int, owner_id: int
) -> MediaFile:
    media = await session.get(MediaFile, media_id)
    if media is None or not await same_team(session, media.owner_id, owner_id):
        raise NotFoundError("媒体文件不存在")
    return media


async def link_media_to_project(
    session: AsyncSession, media: MediaFile, project: Project
) -> None:
    if not await same_team(session, media.owner_id, project.owner_id):
        raise NotFoundError("媒体文件不存在")
    exists = await session.scalar(select(ProjectMediaLink.id).where(
        ProjectMediaLink.project_id == project.id,
        ProjectMediaLink.media_file_id == media.id,
    ))
    if exists is not None:
        return
    session.add(ProjectMediaLink(project_id=project.id, media_file_id=media.id))
    await session.flush()
