"""Verify pinned media without depending on the current production plan."""

# ruff: noqa: RUF001 -- Chinese user-facing punctuation is intentional.

import asyncio
import math
from hashlib import file_digest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.models import (
    AssetVersion,
    Episode,
    MediaFile,
    ProjectMediaLink,
    SegmentVideoVersion,
    VideoSegment,
)
from app.services.episode_edit_contract import EditDocument
from app.services.media_core_service import probe_media_file
from app.services.team_access import same_team


async def list_project_video_sources(session: AsyncSession, *, project_id: int, owner_id: int):
    rows = (
        await session.execute(
            select(SegmentVideoVersion, VideoSegment, Episode, MediaFile)
            .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
            .join(Episode, Episode.id == VideoSegment.episode_id)
            .join(MediaFile, MediaFile.id == SegmentVideoVersion.media_file_id)
            .where(
                Episode.project_id == project_id,
                SegmentVideoVersion.is_final.is_(True),
                MediaFile.kind == "video",
                VideoSegment.status != "archived",
            )
            .order_by(Episode.number, VideoSegment.order, SegmentVideoVersion.id)
        )
    ).all()
    result = []
    for version, segment, episode, media in rows:
        if await same_team(session, owner_id, media.owner_id):
            result.append(
                {
                    "video_version_id": version.id,
                    "media_file_id": media.id,
                    "episode_id": episode.id,
                    "label": f"第{episode.number}集 · 片段{segment.order} · V{version.version}",
                    "duration": media.duration,
                }
            )
    return result


async def verify_project_sources(
    session: AsyncSession,
    *,
    project_id: int,
    owner_id: int,
    document: EditDocument,
    expected_evidence: dict | None = None,
    captured_evidence: dict | None = None,
) -> dict[int, int]:
    kinds = {}
    for clip in document.clips:
        if clip.media_file_id is None:
            continue
        kind = "video" if clip.track == "video" else "audio"
        if clip.media_file_id in kinds and kinds[clip.media_file_id] != kind:
            raise ValidationError("素材类型冲突")
        kinds[clip.media_file_id] = kind
        if kind == "video":
            version = await session.scalar(
                select(SegmentVideoVersion)
                .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
                .join(Episode, Episode.id == VideoSegment.episode_id)
                .where(
                    SegmentVideoVersion.id == clip.video_version_id,
                    SegmentVideoVersion.media_file_id == clip.media_file_id,
                    Episode.project_id == project_id,
                )
            )
            if version is None:
                raise ValidationError("视频版本不属于当前项目或素材不匹配")
            if (
                expected_evidence is not None
                and str(clip.media_file_id) not in expected_evidence
                and not version.is_final
            ):
                raise ValidationError("新增视频请先采用可用版本")
    root = settings.storage_path.resolve()
    frames = {}
    for media_id, kind in kinds.items():
        media = await session.get(MediaFile, media_id)
        if (
            media is None
            or media.kind != kind
            or not await same_team(session, owner_id, media.owner_id)
        ):
            raise ValidationError("素材不可用或无权使用")
        if media.project_id != project_id:
            link = await session.scalar(
                select(ProjectMediaLink.id).where(
                    ProjectMediaLink.project_id == project_id,
                    ProjectMediaLink.media_file_id == media_id,
                )
            )
            if link is None:
                raise ValidationError("素材未加入当前项目")
        if kind == "audio" and str(media_id) not in (expected_evidence or {}):
            versions = (
                await session.scalars(
                    select(AssetVersion).where(AssetVersion.media_file_id == media_id)
                )
            ).all()
            if versions and not any(
                version.is_final and version.review_status != "archived" for version in versions
            ):
                raise ValidationError("新增声音素材尚未采用或已归档")
        path = (root / Path(media.file_path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValidationError("素材文件不存在或路径越界")
        before = path.stat()
        metadata = await probe_media_file(path, kind)
        duration = metadata.get("duration")
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration <= 0
            or (kind == "video" and (not metadata.get("width") or not metadata.get("height")))
        ):
            raise ValidationError("素材无法解码或时长无效")
        frames[media_id] = round(duration * document.frame_rate)

        def digest_file(source_path=path):
            with source_path.open("rb") as stream:
                return file_digest(stream, "sha256").hexdigest()

        digest = await asyncio.to_thread(digest_file)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ConflictError("素材正在变化，请稍后重试")
        evidence = {
            "sha256": digest,
            "kind": kind,
            "path": media.file_path,
            "size": after.st_size,
            "frames": frames[media_id],
        }
        previous = (expected_evidence or {}).get(str(media_id))
        if previous is not None and evidence != previous:
            raise ConflictError("工程引用的素材内容已变化，请重新核对素材")
        if captured_evidence is not None:
            captured_evidence[str(media_id)] = evidence
    return frames
