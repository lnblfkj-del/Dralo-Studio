"""R1 snapshots bind script/refs/timing to existing segment and job identities."""

import math
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models import (
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    MediaFile,
    Project,
    SegmentScriptSnapshot,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.services.asset_binding_service import binding_role, resolve_asset_binding
from app.services.asset_production_service import fingerprint, read_production
from app.services.team_access import same_team


def script_state(segment: VideoSegment) -> str:
    if (segment.parameters or {}).get("legacy_compatibility"):
        return "legacy_unreviewed"
    return "draft" if (segment.prompt or "").strip() else "empty"


def generation_content(content: dict[str, Any]) -> dict[str, Any]:
    """Exclude archive UI statistics, which change when snapshots are captured."""
    result = deepcopy(content)
    for asset in result.get("assets", []):
        production = asset.get("production")
        if isinstance(production, dict):
            production.pop("archive_impact", None)
    return result


def script_content(segment: VideoSegment) -> dict[str, Any]:
    times = {
        key: float(getattr(segment, key))
        for key in ("generation_duration", "timeline_duration", "trim_in", "trim_out")
    }
    if any(not math.isfinite(value) or value < 0 for value in times.values()):
        raise ValidationError("片段时长必须是有限的非负秒数")
    if times["generation_duration"] <= 0 or times["timeline_duration"] <= 0:
        raise ValidationError("生成时长和采用时长必须大于零")
    return deepcopy(
        {
            "schema_version": 1,
            "segment_id": segment.id,
            "lineage_key": segment.lineage_key,
            "parent_lineage_keys": segment.parent_lineage_keys or [],
            "plan_id": segment.plan_id,
            "episode_id": segment.episode_id,
            "order": segment.order,
            "title": segment.title,
            "prompt": segment.prompt,
            "negative_prompt": segment.negative_prompt,
            "parameters": segment.parameters or {},
            "refs": segment.refs or {},
            "script_state": script_state(segment),
            "time_unit": "seconds",
            **times,
        }
    )


def export_subtitle_source(
    segments: list[VideoSegment],
    links_by_segment: dict[int, list[tuple[VideoSegmentShot, Shot]]],
    *,
    include_empty: bool = False,
) -> list[dict[str, Any]]:
    """Map shot dialogue onto the adopted episode timeline."""
    result = []
    episode_cursor = 0.0
    for segment in segments:
        adopted_start = float(segment.trim_in)
        adopted_end = adopted_start + float(segment.timeline_duration)
        for link, shot in links_by_segment[segment.id]:
            dialogue = (shot.dialogue or "").strip()
            start = max(float(link.start_time), adopted_start)
            end = min(float(link.end_time), adopted_end)
            if (not dialogue and not include_empty) or end - start <= 0.001:
                continue
            result.append({
                "segment_id": segment.id,
                "shot_id": shot.id,
                "text": dialogue,
                "start_time": episode_cursor + start - adopted_start,
                "end_time": episode_cursor + end - adopted_start,
            })
        episode_cursor += float(segment.timeline_duration)
    return result


def apply_dialogue_cue_overrides(
    canonical: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply saved cue edits only to relations present on the active plan."""
    by_key = {
        (int(item["segment_id"]), int(item["shot_id"])): item
        for item in overrides
        if isinstance(item, dict) and item.get("segment_id") and item.get("shot_id")
    }
    result = []
    for source in canonical:
        item = dict(source)
        override = by_key.get((item["segment_id"], item["shot_id"]))
        if override:
            item.update({
                key: override[key]
                for key in (
                    "speaker_asset_id", "voice_asset_id", "text", "start_time", "end_time",
                    "audio_media_id", "audio_mode", "native_dialogue_mix_confirmed", "gain",
                )
                if key in override
            })
        result.append(item)
    return result


async def build_script_snapshot_content(
    session: AsyncSession, segment: VideoSegment
) -> dict[str, Any]:
    """Build the canonical live script/asset evidence without persisting it."""
    from app.models import Project
    from app.services import asset_service

    episode = await session.get(Episode, segment.episode_id)
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    if episode is None or plan is None or plan.episode_id != episode.id:
        raise ValidationError("片段与分集/计划关联无效")
    project = await session.get(Project, episode.project_id)
    content = script_content(segment)
    content["source_script_revision"] = plan.source_script_revision
    content["plan_source_type"] = plan.source_type
    content["parent_plan_id"] = plan.parent_plan_id
    assets = []
    if not isinstance(content["refs"], dict) or not isinstance(
        content["refs"].get("asset_bindings", []), list
    ):
        raise ValidationError("片段资产引用结构无效")
    canonical_bindings = []
    scene_ids = list(dict.fromkeys((await session.scalars(
        select(Shot.scene_id)
        .join(VideoSegmentShot, VideoSegmentShot.shot_id == Shot.id)
        .where(VideoSegmentShot.segment_id == segment.id)
        .order_by(VideoSegmentShot.order)
    )).all()))
    for binding in content["refs"].get("asset_bindings", []):
        if not isinstance(binding, dict):
            raise ValidationError("资产绑定必须是对象")
        asset_id = binding.get("asset_id")
        version_id = binding.get("asset_version_id")
        if type(asset_id) is not int or type(version_id) is not int:
            raise ValidationError("资产引用需要稳定资产ID和版本ID")
        role = binding_role(binding)
        canonical = await resolve_asset_binding(
            session,
            project,
            asset_id=asset_id,
            adoption_key=binding.get("adoption_key"),
            asset_version_id=version_id,
            media_file_id=binding.get("media_file_id"),
            role=role,
        )
        canonical_bindings.append({**binding, **canonical})
        asset = await asset_service.get_asset(session, project.id, asset_id)
        assets.append(
            {
                "id": asset.id,
                "name": asset.name,
                "adoption_key": canonical["adoption_key"],
                "version_id": canonical["asset_version_id"],
                "media_file_id": canonical["media_file_id"],
                "media_kind": canonical["media_kind"],
                "resolved_revision": canonical["resolved_revision"],
                "production": await read_production(
                    session,
                    project,
                    asset.id,
                    segment_id=segment.id,
                    scene_ids=scene_ids,
                    include_archive_impact=False,
                ),
            }
        )
    content["refs"]["asset_bindings"] = canonical_bindings
    content["assets"] = assets
    return generation_content(content)


async def capture_script(session: AsyncSession, segment: VideoSegment) -> SegmentScriptSnapshot:
    content = await build_script_snapshot_content(session, segment)
    digest = fingerprint(content)
    query = select(SegmentScriptSnapshot).where(
        SegmentScriptSnapshot.segment_id == segment.id, SegmentScriptSnapshot.fingerprint == digest
    )
    existing = await session.scalar(query)
    if existing is not None:
        return existing
    snapshot = SegmentScriptSnapshot(segment_id=segment.id, fingerprint=digest, content=content)
    try:
        async with session.begin_nested():
            session.add(snapshot)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(query)
        if existing is None:
            raise
        return existing
    return snapshot


async def export_snapshot(
    session: AsyncSession, episode: Episode, segments: list[VideoSegment], selected: dict[int, Any]
) -> dict[str, Any]:
    rows = []
    for segment in segments:
        version = selected[segment.id]
        if version.segment_id != segment.id:
            raise ValidationError("整集引用了其他片段的视频版本")
        media = await session.get(MediaFile, version.media_file_id)
        if (
            media is None
            or media.kind != "video"
            or not await same_team(session, episode.owner_id, media.owner_id)
        ):
            raise ValidationError("成片素材不存在或无权访问")
        rows.append(
            {**script_content(segment), "video_version_id": version.id, "media_file_id": media.id}
        )
    project = await session.get(Project, episode.project_id)
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    settings = (production.settings if production else {}) or {}
    creation = (project.creation_settings if project else {}) or {}
    aspect_ratio = settings.get("aspect_ratio", "project")
    if aspect_ratio == "project":
        aspect_ratio = creation.get("aspect_ratio", "default")
    if aspect_ratio in (None, "", "default", "project"):
        aspect_ratio = "16:9"
    resolution = settings.get("resolution", "project")
    if resolution == "project":
        resolution = creation.get("resolution", "720p")
    resolution = str(resolution).lower()
    if resolution == "4k":
        short_edge = 2160
    elif resolution.endswith("p") and resolution[:-1].isdigit():
        short_edge = max(240, min(int(resolution[:-1]), 4320))
    else:
        resolution = "720p"
        short_edge = 720
    frame_rate = settings.get("frame_rate", 24)
    if frame_rate not in {24, 30}:
        frame_rate = 24
    dimensions = {
        "16:9": (short_edge * 16 // 9, short_edge),
        "21:9": (short_edge * 7 // 3, short_edge),
        "9:16": (short_edge, short_edge * 16 // 9),
        "1:1": (short_edge, short_edge),
        "4:3": (short_edge * 4 // 3, short_edge),
        "3:4": (short_edge, short_edge * 4 // 3),
    }
    if aspect_ratio not in dimensions:
        aspect_ratio = "16:9"
    width, height = dimensions[aspect_ratio]
    width -= width % 2
    height -= height % 2

    segment_ids = [segment.id for segment in segments]
    links = list((await session.execute(
        select(VideoSegmentShot, Shot)
        .join(Shot, Shot.id == VideoSegmentShot.shot_id)
        .where(VideoSegmentShot.segment_id.in_(segment_ids))
        .order_by(VideoSegmentShot.segment_id, VideoSegmentShot.order)
    )).all()) if segment_ids else []
    links_by_segment: dict[int, list[tuple[VideoSegmentShot, Shot]]] = {
        segment.id: [] for segment in segments
    }
    for link, shot in links:
        links_by_segment[link.segment_id].append((link, shot))
    canonical_dialogue_cues = [
        {
            **item,
            "speaker_asset_id": None,
            "voice_asset_id": None,
            "audio_media_id": None,
            "audio_mode": "replace",
            "native_dialogue_mix_confirmed": False,
            "gain": 1.0,
        }
        for item in export_subtitle_source(segments, links_by_segment, include_empty=True)
    ]
    dialogue_cues = apply_dialogue_cue_overrides(
        canonical_dialogue_cues,
        settings.get("dialogue_cues") or [],
    )
    if any(
        item.get("audio_media_id") is not None
        and item.get("audio_mode", "replace") == "mix"
        and not item.get("native_dialogue_mix_confirmed", False)
        for item in dialogue_cues
    ):
        raise ValidationError("保留原声并叠加后期对白前，必须确认接受可能出现双声")
    subtitle_source = [
        {
            "segment_id": item["segment_id"],
            "shot_id": item["shot_id"],
            "text": str(item.get("text") or "").strip(),
            "start_time": float(item["start_time"]),
            "end_time": float(item["end_time"]),
        }
        for item in dialogue_cues
        if str(item.get("text") or "").strip()
        and float(item["end_time"]) - float(item["start_time"]) > 0.001
    ]
    sound_cues = [
        {
            "cue_id": str(item["cue_id"]),
            "kind": str(item["kind"]),
            "label": str(item.get("label") or ""),
            "audio_media_id": int(item["audio_media_id"]),
            "start_time": float(item["start_time"]),
            "end_time": float(item["end_time"]),
            "gain": float(item.get("gain", 1)),
            "loop": bool(item.get("loop", False)),
        }
        for item in settings.get("sound_cues") or []
    ]
    from app.services.production_dialogue_service import validate_export_dialogue
    audio_sources = await validate_export_dialogue(session, episode, dialogue_cues)
    return {
        "schema_version": 1,
        "time_unit": "seconds",
        "episode_id": episode.id,
        "source_script_revision": episode.script_revision,
        "segments": rows,
        "output_spec": {
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
            "sample_rate": 48000,
            "subtitle_language": "eng" if creation.get("market") == "overseas" else "zho",
        },
        "subtitle_source": subtitle_source,
        "dialogue_cues": dialogue_cues,
        "dialogue_audio_sources": audio_sources,
        "dialogue_duplicate_audio_policy": {
            "default_mode": "replace",
            "replace_native_audio_in_cue_interval": True,
            "mix_requires_explicit_confirmation": True,
        },
        "sound_cues": sound_cues,
        "audio_mix_order": ["native", "background_music", "ambience", "dialogue", "sfx"],
    }
