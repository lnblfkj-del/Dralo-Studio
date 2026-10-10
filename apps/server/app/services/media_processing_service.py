"""Media storage writes, thumbnails and generated video finalization."""

import asyncio
import contextlib
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage_safety import require_storage_capacity, storage_reservation, write_storage_bytes
from app.core.media_quota import require_media_budget
from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Job, MediaFile, Project, ProjectMediaLink, SegmentVideoVersion, Shot,
    ShotVideoVersion, VideoSegment,
)
from app.services.media_core_service import MEDIA_RULES, classify_upload, ensure_media_metadata, probe_media_file, validate_decodable_media

from app.core.logging import get_logger

logger = get_logger(__name__)

async def get_video_thumbnail(media: MediaFile) -> Path:
    if media.kind != "video":
        raise ConflictError("只有视频文件支持生成预览图")
    storage_root = settings.storage_path.resolve()
    source = (storage_root / Path(media.file_path)).resolve()
    if not source.is_relative_to(storage_root) or not source.is_file():
        raise NotFoundError("媒体文件不存在")
    cache_dir = (settings.storage_path / "cache" / "thumbnails" / str(media.owner_id)
                 if settings.runtime_execution_location == "cloud"
                 else Path(gettempdir()) / "video-canvas-thumbnails" / str(media.owner_id))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = (media.hash or str(media.id))[:16]
    target = cache_dir / f"{media.id}-{cache_key}.jpg"
    if target.is_file():
        return target
    temporary = cache_dir / f".{media.id}-{uuid4().hex}.jpg"
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-max_alloc", "268435456", "-ss", "0.1",
            "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2",
            "-q:v", "3", "-y", str(temporary),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0 or not temporary.is_file():
            raise ConflictError("视频预览图生成失败")
        temporary.replace(target)
        return target
    except asyncio.TimeoutError as exc:
        raise ConflictError("视频预览图生成超时") from exc
    finally:
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
        temporary.unlink(missing_ok=True)


async def ensure_segment_last_frame(
    session: AsyncSession, version: SegmentVideoVersion
) -> MediaFile:
    """Persist and cache the exact tail frame used by downstream segments."""
    cached_id = (
        ((version.parameters or {}).get("continuity_frames") or {}).get(
            "last_frame_media_id"
        )
    )
    cached = await session.get(MediaFile, cached_id) if type(cached_id) is int else None
    if cached is not None:
        path = (settings.storage_path.resolve() / Path(cached.file_path)).resolve()
        if (
            path.is_relative_to(settings.storage_path.resolve())
            and path.is_file()
            and cached.kind == "image"
            and cached.width
            and cached.height
        ):
            return cached

    source = await session.get(MediaFile, version.media_file_id)
    if source is None or source.kind != "video" or source.project_id is None:
        raise ConflictError("前序片段采用视频不存在，无法提取连续尾帧")
    await ensure_media_metadata(session, source)
    validate_decodable_media(source, "video")
    storage_root = settings.storage_path.resolve()
    source_path = (storage_root / Path(source.file_path)).resolve()
    if not source_path.is_relative_to(storage_root) or not source_path.is_file():
        raise ConflictError("前序片段采用视频文件不存在，无法提取连续尾帧")

    relative_dir = Path("projects") / str(source.project_id) / "continuity_frames"
    absolute_dir = settings.storage_path / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)
    filename = f"segment-version-{version.id}-{uuid4().hex}.png"
    relative_path = relative_dir / filename
    output = settings.storage_path / relative_path
    temporary = output.with_name(f".{filename}.tmp.png")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file",
            "-max_alloc",
            "268435456",
            "-sseof",
            "-0.05",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-update",
            "1",
            str(temporary),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise ConflictError("前序片段尾帧提取失败，请检查采用视频")
        temporary.replace(output)
        metadata = await probe_media_file(output, "image")
        media = MediaFile(
            project_id=source.project_id,
            owner_id=source.owner_id,
            kind="image",
            source="generation",
            file_path=relative_path.as_posix(),
            original_name=filename,
            mime_type="image/png",
            size=output.stat().st_size,
            width=metadata["width"],
            height=metadata["height"],
            duration=None,
            hash=sha256(output.read_bytes()).hexdigest(),
        )
        validate_decodable_media(media, "image")
        session.add(media)
        await session.flush()
        session.add(
            ProjectMediaLink(project_id=source.project_id, media_file_id=media.id)
        )
        version.parameters = {
            **(version.parameters or {}),
            "continuity_frames": {
                **((version.parameters or {}).get("continuity_frames") or {}),
                "last_frame_media_id": media.id,
                "source_video_media_id": source.id,
            },
        }
        await session.flush()
        return media
    except asyncio.TimeoutError as exc:
        raise ConflictError("前序片段尾帧提取超时") from exc
    finally:
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
        temporary.unlink(missing_ok=True)


async def save_upload(
    session: AsyncSession,
    *,
    owner_id: int,
    stream: UploadFile,
    filename: str,
    content_type: str | None,
    project: Project | None,
) -> MediaFile:
    _, maximum, _ = classify_upload(filename, content_type)
    size = stream.size if stream.size is not None else maximum
    if not 0 <= size <= maximum:
        raise ConflictError("上传文件超过允许大小")
    with storage_reservation(settings, size, target=settings.storage_path):
        return await _save_upload(session, owner_id=owner_id, stream=stream, filename=filename,
                                  content_type=content_type, project=project, reserved_size=size)


async def _save_upload(
    session: AsyncSession,
    *,
    owner_id: int,
    stream: UploadFile,
    filename: str,
    content_type: str | None,
    project: Project | None,
    reserved_size: int,
) -> MediaFile:
    kind, max_bytes, safe_name = classify_upload(filename, content_type)
    from app.services.upload_quota_service import remaining_upload_bytes
    remaining = await remaining_upload_bytes(session)
    if remaining == 0:
        raise ConflictError("工作空间上传素材额度已用完，请清理素材或联系管理员")
    extension = Path(safe_name).suffix.lower()
    relative_dir = Path("users") / str(owner_id) / "uploads"
    absolute_dir = settings.storage_path / relative_dir
    require_storage_capacity(settings, target=absolute_dir)
    absolute_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = absolute_dir / f".{uuid4().hex}.upload"
    from app.services.upload_temp_cleanup import upload_lock
    from filelock import Timeout
    staging_lock = upload_lock(absolute_dir)
    try:
        staging_lock.acquire(timeout=0)
    except Timeout as exc:
        raise ConflictError("当前上传目录正在处理，请稍后重试") from exc
    final_path = None
    digest = sha256()
    size = 0
    try:
        with temporary_path.open("wb") as target:
            while chunk := await stream.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes or size > reserved_size:
                    raise ConflictError(f"{kind} 文件超过允许大小")
                if remaining is not None and size > remaining:
                    raise ConflictError("本次文件超过工作空间剩余上传额度，未保存本次上传")
                require_storage_capacity(settings, len(chunk), target=temporary_path)
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise ConflictError("上传文件不能为空")
        file_hash = digest.hexdigest()
        # 每次上传都创建独立素材。原始文件名和文件内容均允许重复，
        # 实际磁盘文件使用 UUID 命名，因此不会发生路径冲突。
        relative_path = relative_dir / f"{uuid4().hex}{extension}"
        final_path = settings.storage_path / relative_path
        temporary_path.replace(final_path)
        metadata = await probe_media_file(final_path, kind)
        media = MediaFile(
            project_id=project.id if project is not None else None,
            owner_id=owner_id,
            kind=kind,
            source="upload",
            file_path=relative_path.as_posix(),
            original_name=safe_name,
            mime_type=content_type,
            size=size,
            width=metadata["width"],
            height=metadata["height"],
            duration=metadata["duration"],
            hash=file_hash,
        )
        session.add(media)
        await session.flush()
        if project is not None:
            session.add(ProjectMediaLink(
                project_id=project.id, media_file_id=media.id
            ))
            await session.flush()
        return media
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise
    finally:
        staging_lock.release()


async def finalize_video_job(
    session: AsyncSession, job: Job, video_bytes: bytes
) -> dict[str, object]:
    """将 Worker 下载的视频安全地写入私有存储，并加入项目媒体库。"""
    if job.project_id is None or not video_bytes or len(video_bytes) > MEDIA_RULES["video"]["max_bytes"]:
        raise ConflictError("视频生成结果为空或超过 500 MB")
    if not video_bytes.startswith((b"\x00\x00\x00", b"RIFF", b"\x1aE\xdf\xa3")):
        raise ConflictError("视频生成结果不是受支持的视频文件")
    relative_path = Path("projects") / str(job.project_id) / "videos" / f"{uuid4().hex}.mp4"
    absolute_path = settings.storage_path / relative_path
    await require_media_budget(session, len(video_bytes))
    require_storage_capacity(settings, len(video_bytes), target=absolute_path)
    temporary_path = absolute_path.with_suffix(".mp4.tmp")
    try:
        write_storage_bytes(settings, absolute_path, video_bytes)
        media = MediaFile(
            project_id=job.project_id, owner_id=job.owner_id, kind="video", source="generation",
            file_path=relative_path.as_posix(), original_name=absolute_path.name,
            mime_type="video/mp4", size=len(video_bytes), hash=sha256(video_bytes).hexdigest(),
        )
        session.add(media)
        await session.flush()
        session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
        await session.flush()
        metadata = await probe_media_file(absolute_path, "video")
        media.duration, media.width, media.height = metadata["duration"], metadata["width"], metadata["height"]
        validate_decodable_media(media, "video")
        result: dict[str, object] = {"media_file_id": media.id, "media_url": f"/api/media/{media.id}", "kind": "video", "duration": media.duration}
        if (job.payload or {}).get("h3_production"):
            expected = (job.payload.get("parameters") or {}).get("aspect_ratio")
            if (isinstance(expected, str) and ":" in expected
                    and media.width and media.height):
                left, right = expected.split(":", 1)
                if left.isdigit() and right.isdigit() and int(right) > 0:
                    actual = media.width / media.height
                    target = int(left) / int(right)
                    result["aspect_ratio_check"] = {
                        "expected": expected,
                        "actual_width": media.width,
                        "actual_height": media.height,
                        "status": "matched" if abs(actual - target) <= 0.02 else "mismatch",
                    }
        if job.target_type == "shot" and job.target_id is not None:
            shot = await session.get(Shot, job.target_id)
            if shot is None:
                raise ConflictError("分镜不存在，无法回流视频结果")
            version = (await session.scalar(select(func.max(ShotVideoVersion.version)).where(ShotVideoVersion.shot_id == shot.id)) or 0) + 1
            await session.execute(update(ShotVideoVersion).where(ShotVideoVersion.shot_id == shot.id).values(is_final=False))
            video_version = ShotVideoVersion(
                shot_id=shot.id, media_file_id=media.id, source_job_id=job.id, version=version,
                prompt=job.payload["prompt"], negative_prompt=job.payload.get("negative_prompt"),
                parameters=job.payload.get("parameters", {}), is_final=True,
            )
            session.add(video_version)
            from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

            if shot.status != SHOT_STATUS_SUPERSEDED:
                shot.status = "ready"
            await session.flush()
            result.update({"shot_id": shot.id, "video_version_id": video_version.id})
        elif job.target_type == "video_segment" and job.target_id is not None:
            segment = await session.get(VideoSegment, job.target_id)
            if segment is None or segment.episode_id != job.payload.get("episode_id"):
                raise ConflictError("视频片段不存在，无法回流生成结果")
            latest = (
                await session.scalar(
                    select(func.max(SegmentVideoVersion.version)).where(
                        SegmentVideoVersion.segment_id == segment.id
                    )
                )
                or 0
            )
            from app.services.segment_video_candidate_service import version_parameters

            version_params = version_parameters(job)
            if "aspect_ratio_check" in result:
                version_params = {**version_params, "aspect_ratio_check": result["aspect_ratio_check"]}
            video_version = SegmentVideoVersion(
                segment_id=segment.id,
                media_file_id=media.id,
                source_job_id=job.id,
                version=latest + 1,
                prompt=job.payload["prompt"],
                negative_prompt=job.payload.get("negative_prompt"),
                parameters=version_params,
                is_final=False,
            )
            session.add(video_version)
            segment.status = "review"
            await session.flush()
            result.update(
                {
                    "segment_id": segment.id,
                    "segment_order": segment.order,
                    "segment_video_version_id": video_version.id,
                    "is_final": video_version.is_final,
                }
            )
        return result
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        absolute_path.unlink(missing_ok=True)
        raise


def delete_files(paths: list[Path]) -> None:
    storage_root = settings.storage_path.resolve()
    for relative_path in paths:
        absolute_path = (storage_root / relative_path).resolve()
        if absolute_path.is_relative_to(storage_root):
            try:
                absolute_path.unlink(missing_ok=True)
            except OSError:
                # 数据库事务已成功提交，文件清理失败只记录待处理项，不能伪装成删除失败。
                logger.warning("媒体文件清理失败，数据库记录已删除: %s", relative_path)
