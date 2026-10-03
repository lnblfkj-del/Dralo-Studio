"""Editable episode dialogue/subtitle cues bound to the active segment plan."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Asset,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    MediaFile,
    ProjectAssetLink,
    ProjectMediaLink,
    Scene,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.services.team_access import same_team


def audio_readiness(duration: float | None, cue_duration: float) -> str:
    if duration is None:
        return "duration_unknown"
    tolerance = 0.15
    if duration > cue_duration + tolerance:
        return "too_long"
    if duration < cue_duration - tolerance:
        return "too_short"
    return "ready"


async def _active_context(
    session: AsyncSession,
    episode: Episode,
) -> tuple[EpisodeProduction, EpisodeProductionPlan, list[VideoSegment], list[tuple[Any, ...]]]:
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is None or production.active_plan_id is None:
        raise NotFoundError("本集尚未建立片段生产计划")
    plan = await session.get(EpisodeProductionPlan, production.active_plan_id)
    if plan is None or plan.episode_id != episode.id:
        raise NotFoundError("本集片段生产计划不存在")
    segments = list((await session.scalars(
        select(VideoSegment)
        .where(VideoSegment.plan_id == plan.id)
        .order_by(VideoSegment.order, VideoSegment.id)
    )).all())
    segment_ids = [item.id for item in segments]
    rows = list((await session.execute(
        select(VideoSegmentShot, Shot, Scene)
        .join(Shot, Shot.id == VideoSegmentShot.shot_id)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(VideoSegmentShot.segment_id.in_(segment_ids))
        .order_by(VideoSegmentShot.segment_id, VideoSegmentShot.order)
    )).all()) if segment_ids else []
    return production, plan, segments, rows


def _canonical_cues(
    segments: list[VideoSegment],
    rows: list[tuple[VideoSegmentShot, Shot, Scene]],
) -> list[dict[str, Any]]:
    rows_by_segment: dict[int, list[tuple[VideoSegmentShot, Shot, Scene]]] = {
        item.id: [] for item in segments
    }
    for link, shot, scene in rows:
        rows_by_segment[link.segment_id].append((link, shot, scene))
    result: list[dict[str, Any]] = []
    episode_cursor = 0.0
    for segment in segments:
        adopted_start = float(segment.trim_in)
        segment_duration = float(segment.timeline_duration)
        adopted_end = adopted_start + segment_duration
        segment_start = episode_cursor
        segment_end = episode_cursor + segment_duration
        for link, shot, scene in rows_by_segment[segment.id]:
            start = max(float(link.start_time), adopted_start)
            end = min(float(link.end_time), adopted_end)
            if end - start <= 0.001:
                continue
            result.append({
                "segment_id": segment.id,
                "segment_order": segment.order,
                "segment_title": segment.title,
                "shot_id": shot.id,
                "shot_order": link.order,
                "scene_name": scene.name,
                "audio_note": shot.audio_note,
                "speaker_asset_id": None,
                "voice_asset_id": None,
                "text": (shot.dialogue or "").strip(),
                "start_time": episode_cursor + start - adopted_start,
                "end_time": episode_cursor + end - adopted_start,
                "segment_start_time": segment_start,
                "segment_end_time": segment_end,
                "audio_media_id": None,
                "audio_mode": "replace",
                "native_dialogue_mix_confirmed": False,
                "gain": 1.0,
            })
        episode_cursor = segment_end
    return result


async def _accessible_audio(
    session: AsyncSession,
    episode: Episode,
    media_id: int,
    *,
    label: str = "对白",
) -> MediaFile:
    media = await session.get(MediaFile, media_id)
    if media is None or media.kind != "audio" or not await same_team(
        session, episode.owner_id, media.owner_id
    ):
        raise ConflictError(f"{label}音频不存在、类型错误或无权使用")
    if media.project_id != episode.project_id:
        linked = await session.scalar(select(ProjectMediaLink.id).where(
            ProjectMediaLink.project_id == episode.project_id,
            ProjectMediaLink.media_file_id == media.id,
        ))
        if linked is None:
            raise ConflictError(f"{label}音频尚未加入当前项目")
    return media


async def _project_asset(
    session: AsyncSession,
    episode: Episode,
    asset_id: int,
    expected_type: str,
) -> Asset:
    asset = await session.scalar(
        select(Asset)
        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
        .where(
            Asset.id == asset_id,
            Asset.asset_type == expected_type,
            ProjectAssetLink.project_id == episode.project_id,
        )
    )
    if asset is None:
        label = "角色" if expected_type == "character" else "音色"
        raise ConflictError(f"对白绑定的{label}资产不存在或尚未加入当前项目")
    return asset


async def validate_dialogue_cues(
    session: AsyncSession,
    episode: Episode,
    cues: list[dict[str, Any]],
) -> None:
    if not cues:
        return
    _production, _plan, segments, rows = await _active_context(session, episode)
    canonical = _canonical_cues(segments, rows)
    allowed = {
        (item["segment_id"], item["shot_id"]): item
        for item in canonical
    }
    media_cache: dict[int, MediaFile] = {}
    asset_cache: dict[tuple[str, int], Asset] = {}
    for cue in cues:
        key = (int(cue["segment_id"]), int(cue["shot_id"]))
        source = allowed.get(key)
        if source is None:
            raise ConflictError("对白配置引用了当前片段计划之外的分镜")
        start = float(cue["start_time"])
        end = float(cue["end_time"])
        if (
            start < source["segment_start_time"] - 0.001
            or end > source["segment_end_time"] + 0.001
            or end - start <= 0.001
        ):
            raise ConflictError("对白时间必须位于所属片段的采用区间内")
        media_id = cue.get("audio_media_id")
        if (
            media_id is not None
            and cue.get("audio_mode", "replace") == "mix"
            and not cue.get("native_dialogue_mix_confirmed", False)
        ):
            raise ConflictError("保留原声并叠加后期对白前，必须确认接受可能出现双声")
        if media_id is not None and media_id not in media_cache:
            media_cache[media_id] = await _accessible_audio(session, episode, media_id)
        for field, asset_type in (
            ("speaker_asset_id", "character"),
            ("voice_asset_id", "voice"),
        ):
            asset_id = cue.get(field)
            key = (asset_type, asset_id)
            if asset_id is not None and key not in asset_cache:
                asset_cache[key] = await _project_asset(session, episode, asset_id, asset_type)


async def validate_sound_cues(
    session: AsyncSession,
    episode: Episode,
    cues: list[dict[str, Any]],
) -> None:
    if not cues:
        return
    _production, _plan, segments, _rows = await _active_context(session, episode)
    episode_duration = sum(float(item.timeline_duration) for item in segments)
    media_cache: dict[int, MediaFile] = {}
    for cue in cues:
        start = float(cue["start_time"])
        end = float(cue["end_time"])
        if start < 0 or end <= start or end > episode_duration + 0.001:
            raise ConflictError("环境声或音效时间必须位于整集采用区间内")
        media_id = int(cue["audio_media_id"])
        if media_id not in media_cache:
            media_cache[media_id] = await _accessible_audio(
                session, episode, media_id, label="环境声/音效",
            )


async def preview_dialogue_cues(
    session: AsyncSession,
    episode: Episode,
) -> dict[str, Any]:
    production, plan, segments, rows = await _active_context(session, episode)
    items = _canonical_cues(segments, rows)
    saved = {
        (int(item["segment_id"]), int(item["shot_id"])): item
        for item in ((production.settings or {}).get("dialogue_cues") or [])
        if isinstance(item, dict) and item.get("segment_id") and item.get("shot_id")
    }
    media_cache: dict[int, MediaFile | None] = {}
    asset_cache: dict[tuple[str, int], Asset | None] = {}
    for item in items:
        override = saved.get((item["segment_id"], item["shot_id"]))
        if override:
            for key in (
                "speaker_asset_id", "voice_asset_id", "text", "start_time", "end_time",
                "audio_media_id", "audio_mode", "native_dialogue_mix_confirmed", "gain",
            ):
                if key in override:
                    item[key] = override[key]
        media_id = item["audio_media_id"]
        media = None
        if media_id is not None:
            if media_id not in media_cache:
                try:
                    media_cache[media_id] = await _accessible_audio(session, episode, media_id)
                except ConflictError:
                    media_cache[media_id] = None
            media = media_cache[media_id]
        item["audio_name"] = media.original_name if media else None
        item["audio_duration"] = float(media.duration) if media and media.duration is not None else None
        item["readiness"] = (
            audio_readiness(item["audio_duration"], float(item["end_time"]) - float(item["start_time"]))
            if media_id is not None
            else "unbound"
        )
        for field, name_field, asset_type in (
            ("speaker_asset_id", "speaker_name", "character"),
            ("voice_asset_id", "voice_asset_name", "voice"),
        ):
            asset_id = item[field]
            asset = None
            if asset_id is not None:
                key = (asset_type, asset_id)
                if key not in asset_cache:
                    asset_cache[key] = await session.scalar(
                        select(Asset)
                        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
                        .where(
                            Asset.id == asset_id,
                            Asset.asset_type == asset_type,
                            ProjectAssetLink.project_id == episode.project_id,
                        )
                    )
                asset = asset_cache[key]
            item[name_field] = asset.name if asset else None
    return {"plan_id": plan.id, "plan_revision": plan.revision, "items": items}
