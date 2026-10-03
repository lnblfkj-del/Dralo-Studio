"""Authenticated media delivery from private project storage."""

from pathlib import Path
import asyncio
from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, PageDep, SessionDep
from app.core.config import settings
from app.core.database import get_session
from app.core.errors import AuthError, NotFoundError
from app.core.security import create_access_token, decode_access_token
from app.schemas.common import Page
from app.schemas.media import (
    EpisodeExportCreate,
    GenerationHistoryOut,
    MediaFileOut,
    MediaProjectLinkCreate,
)
from app.services import media_service, project_service, user_service

router = APIRouter(prefix="/media", tags=["media"])

# Release read-only transactions before audit writes and file streaming.
MediaReadSession = Annotated[AsyncSession, Depends(get_session, scope="function")]


@router.get("/storage-usage")
async def personal_storage_usage(session: SessionDep, user: CurrentUser):
    from app.core.errors import ConflictError
    if settings.runtime_execution_location != "cloud":
        raise ConflictError("独立部署不启用云端账号素材额度管理")
    from app.services import account_quota_service
    return await account_quota_service.usage(session, user.id)


def _private_media_path(media) -> Path:
    root = settings.storage_path.resolve()
    path = (root / Path(media.file_path)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFoundError("媒体文件不存在")
    return path


def _playback_version(media) -> str:
    path = _private_media_path(media)
    stat = path.stat()
    # A cheap version stamp, not a content checksum: avoid reading large videos
    # on every Range request. Media writes must replace files, not edit in place.
    stamp = f"{media.file_path}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_ino}"
    return sha256(stamp.encode()).hexdigest()


def _playback_response(media, payload) -> FileResponse:
    if payload.get("mv") != _playback_version(media):
        raise AuthError("媒体文件版本已变化，请重新获取播放链接")
    path = _private_media_path(media)
    if payload.get("pv"):
        from app.services.video_preview_service import require_preview
        path, meta = require_preview(media)
        if payload["pv"] != meta["version"]:
            raise AuthError("视频预览版本已变化")
    return FileResponse(path, media_type="video/mp4" if payload.get("pv") else media.mime_type, headers={
        "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
    })


@router.get("/playback")
async def playback_media(token: str, session: MediaReadSession) -> FileResponse:
    payload = decode_access_token(token)
    if payload.get("scope") != "media-playback":
        raise AuthError("媒体播放凭证无效")
    try:
        media_id = int(payload["media_id"])
        owner_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("媒体播放凭证无效") from exc
    owner = await user_service.get_by_id(session, owner_id)
    if owner is None or not owner.is_active:
        raise AuthError("媒体所属账号不可用")
    if payload.get("sv", 0) != owner.session_version or owner.must_change_password:
        raise AuthError("媒体播放凭证已失效")
    from app.core.workspace_context import isolation_enabled, workspace_scope
    if isolation_enabled():
        from app.services.workspace_service import require_membership
        member = await require_membership(session, str(payload.get("workspace_id") or ""), owner_id)
        with workspace_scope(member.workspace_id, owner_id, member.role):
            media = await media_service.get_owned_media(session, media_id, owner_id)
        return _playback_response(media, payload)
    from app.services.permission_service import allowed
    if not allowed(owner, "projects.view"):
        raise AuthError("无团队素材查看权限")
    media = await media_service.get_owned_media(session, media_id, owner_id)
    return _playback_response(media, payload)


@router.post("/{media_id}/playback-url")
async def create_playback_url(
    media_id: int, session: MediaReadSession, user: CurrentUser, preview: bool = False
) -> dict:
    media = await media_service.get_owned_media(session, media_id, user.id)
    from app.core.workspace_context import isolation_enabled, required_workspace
    workspace_claim = {"workspace_id": required_workspace().workspace_id} if isolation_enabled() else {}
    proxy = None
    if preview and media.kind == "video":
        from app.services.video_preview_service import request_preview
        proxy = await request_preview(media)
    token = create_access_token(
        str(user.id),
        extra_claims={"scope": "media-playback", "media_id": media_id, "mv": _playback_version(media), "sv": user.session_version, **workspace_claim,
                      **({"pv": proxy[1]["version"]} if proxy else {})},
        expires_minutes=30,
    )
    return {"url": f"/api/media/playback?token={token}", "expires_in": 1800,
            "preview_url": f"/api/media/{media_id}/preview" if proxy else None, "preview_ready": bool(proxy)}


@router.get("/{media_id}/preview")
async def preview_media(media_id: int, session: MediaReadSession, user: CurrentUser):
    from app.services.video_preview_service import require_preview
    media = await media_service.get_owned_media(session, media_id, user.id)
    path, _ = require_preview(media)
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/preview/detail")
async def preview_detail(media_id: int, session: MediaReadSession, user: CurrentUser):
    from app.services.video_preview_service import require_preview, CHUNK_SIZE
    media = await media_service.get_owned_media(session, media_id, user.id)
    _, meta = require_preview(media)
    return JSONResponse({"id": media.id, "size": meta["size"], "version": meta["version"], "chunk_size": CHUNK_SIZE},
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/preview/chunks/{index}")
async def preview_chunk(media_id: int, index: int, session: MediaReadSession, user: CurrentUser):
    from app.services.video_preview_service import chunk
    media = await media_service.get_owned_media(session, media_id, user.id)
    raw, _ = await asyncio.to_thread(chunk, media, index)
    return Response(raw, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/preview/chunks/{index}/detail")
async def preview_chunk_detail(media_id: int, index: int, session: MediaReadSession, user: CurrentUser):
    from app.services.video_preview_service import chunk
    media = await media_service.get_owned_media(session, media_id, user.id)
    _, meta = await asyncio.to_thread(chunk, media, index)
    return JSONResponse(meta, headers={"Cache-Control": "private, no-store"})


@router.get("", response_model=Page[MediaFileOut])
async def list_media(
    session: SessionDep,
    user: CurrentUser,
    page: PageDep,
    kind: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    project_id: int | None = None,
    global_only: bool = False,
) -> Page[MediaFileOut]:
    if kind is not None and kind not in media_service.MEDIA_RULES:
        raise NotFoundError("媒体类型不存在")
    if source is not None and source not in {"upload", "generation", "export", "processing"}:
        raise NotFoundError("媒体来源不存在")
    if project_id is not None:
        await project_service.get_project(session, project_id, user.id)
    items, total = await media_service.list_media(
        session,
        user.id,
        limit=page.page_size,
        offset=page.offset,
        kind=kind,
        source=source,
        keyword=keyword,
        project_id=project_id,
        global_only=global_only,
    )
    return Page[MediaFileOut](
        items=[MediaFileOut.model_validate(await media_service.to_media_out(session, item)) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/upload", response_model=MediaFileOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile,
    session: SessionDep,
    user: CurrentUser,
    project_id: int | None = Form(default=None),
    purpose: Literal["creative", "style"] = Form(default="creative"),
) -> MediaFileOut:
    if purpose == "style" and (project_id is not None or media_service.classify_upload(file.filename or "", file.content_type)[0] != "image"):
        raise NotFoundError("风格专用上传仅支持全局图片")
    project = (
        await project_service.get_project(session, project_id, user.id)
        if project_id is not None
        else None
    )
    try:
        media = await media_service.save_upload(
            session,
            owner_id=user.id,
            stream=file,
            filename=file.filename or "",
            content_type=file.content_type,
            project=project,
        )
        media.purpose = purpose
        await session.commit()
        return MediaFileOut.model_validate(await media_service.to_media_out(session, media))
    finally:
        await file.close()


@router.post("/export/episodes/{episode_id}", response_model=MediaFileOut, status_code=status.HTTP_201_CREATED)
async def export_episode(
    episode_id: int, payload: EpisodeExportCreate, session: SessionDep, user: CurrentUser
) -> MediaFileOut:
    media = await media_service.export_episode_video(
        session,
        user.id,
        episode_id,
        background_audio_media_id=payload.background_audio_media_id,
        background_audio_volume=payload.background_audio_volume,
        include_subtitles=payload.include_subtitles,
    )
    await session.commit()
    return MediaFileOut.model_validate(await media_service.to_media_out(session, media))

@router.get("/generation-history", response_model=Page[GenerationHistoryOut])
async def generation_history(
    session: SessionDep,
    user: CurrentUser,
    page: PageDep,
    job_status: str | None = None,
    project_id: int | None = None,
    keyword: str | None = None,
) -> Page[GenerationHistoryOut]:
    if project_id is not None:
        await project_service.get_project(session, project_id, user.id)
    items, total = await media_service.list_generation_history(
        session,
        user.id,
        limit=page.page_size,
        offset=page.offset,
        status=job_status,
        project_id=project_id,
        keyword=keyword,
    )
    return Page[GenerationHistoryOut](
        items=[GenerationHistoryOut.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/{media_id}/thumbnail")
async def get_media_thumbnail(
    media_id: int, session: SessionDep, user: CurrentUser
) -> FileResponse:
    media = await media_service.get_owned_media(session, media_id, user.id)
    from app.services.image_thumbnail_service import get_thumbnail
    path = await get_thumbnail(media)
    return FileResponse(path, media_type="image/jpeg", filename=f"media-{media.id}-thumbnail.jpg",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/thumbnail/detail")
async def get_thumbnail_detail(media_id: int, session: SessionDep, user: CurrentUser):
    from hashlib import sha256
    from app.services.image_thumbnail_service import get_thumbnail
    media = await media_service.get_owned_media(session, media_id, user.id)
    path = await get_thumbnail(media)
    raw = await asyncio.to_thread(path.read_bytes)
    return JSONResponse({"id": media.id, "size": len(raw), "hash": sha256(raw).hexdigest(),
                         "mime_type": "image/jpeg", "updated_at": path.name},
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/waveform")
async def get_media_waveform(media_id: int, session: MediaReadSession, user: CurrentUser):
    from app.services.audio_waveform_service import get_waveform
    media = await media_service.get_owned_media(session, media_id, user.id)
    path = await get_waveform(media)
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/waveform/detail")
async def get_waveform_detail(media_id: int, session: MediaReadSession, user: CurrentUser):
    from app.services.audio_waveform_service import get_waveform
    media = await media_service.get_owned_media(session, media_id, user.id)
    path = await get_waveform(media)
    raw = await asyncio.to_thread(path.read_bytes)
    return JSONResponse({"id": media.id, "size": len(raw), "hash": sha256(raw).hexdigest(),
                         "mime_type": "application/json", "updated_at": path.name},
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{media_id}/detail", response_model=MediaFileOut)
async def get_media_detail(
    media_id: int, session: SessionDep, user: CurrentUser
) -> MediaFileOut:
    media = await media_service.get_owned_media(session, media_id, user.id)
    await media_service.ensure_media_metadata(session, media)
    await session.commit()
    return MediaFileOut.model_validate(await media_service.to_media_out(session, media))


@router.post("/{media_id}/projects", response_model=MediaFileOut)
async def link_media_to_project(
    media_id: int,
    payload: MediaProjectLinkCreate,
    session: SessionDep,
    user: CurrentUser,
) -> MediaFileOut:
    media = await media_service.get_owned_media(session, media_id, user.id)
    project = await project_service.get_project(session, payload.project_id, user.id)
    await media_service.link_media_to_project(session, media, project)
    await session.commit()
    return MediaFileOut.model_validate(await media_service.to_media_out(session, media))


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    media_id: int, session: SessionDep, user: CurrentUser
) -> None:
    media = await media_service.get_owned_media(session, media_id, user.id)
    path = await media_service.delete_media(session, media)
    await session.commit()
    if settings.runtime_execution_location == "cloud":
        from app.services.storage_cleanup_service import drain_media
        await drain_media()
    else:
        media_service.delete_files([path])


@router.get("/{media_id}")
async def get_media(media_id: int, session: MediaReadSession, user: CurrentUser) -> FileResponse:
    media = await media_service.get_owned_media(session, media_id, user.id)
    storage_root = settings.storage_path.resolve()
    path = (storage_root / Path(media.file_path)).resolve()
    if not path.is_relative_to(storage_root) or not path.is_file():
        raise NotFoundError("媒体文件不存在")
    return FileResponse(path, media_type=media.mime_type, headers={"Cache-Control": "private, no-store"})
