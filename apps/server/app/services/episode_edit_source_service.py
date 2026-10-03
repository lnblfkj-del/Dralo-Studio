"""Read-only E5 timeline projection and server-verified T1 media sources."""

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    MediaFile,
    ProjectMediaLink,
    SegmentVideoVersion,
    VideoSegment,
)
from app.services.episode_edit_contract import EditDocument, validate_document
from app.services.media_core_service import probe_media_file
from app.services.production_snapshot_service import export_snapshot
from app.services.team_access import same_team


def assert_legacy_export_parity(
    document: EditDocument, snapshot: dict[str, Any], production_settings: dict[str, Any],
) -> None:
    """Reject a draft seed that already differs from the E5 export timeline."""
    fps = document.frame_rate
    video = sorted(
        (clip for clip in document.clips if clip.track == "video"),
        key=lambda clip: clip.timeline_start_frame,
    )
    segments = snapshot["segments"]
    if len(video) != len(segments):
        raise ConflictError("剪辑主轨与当前导出片段数量不一致")
    seconds_cursor = 0.0
    for clip, segment in zip(video, segments, strict=True):
        trim_in = float(segment["trim_in"])
        duration = float(segment["timeline_duration"])
        if (
            clip.clip_id != f"segment:{segment['segment_id']}"
            or clip.media_file_id != segment["media_file_id"]
            or clip.video_version_id != segment["video_version_id"]
            or clip.source_in_frame != round(trim_in * fps)
            or clip.source_out_frame != round((trim_in + duration) * fps)
            or abs(clip.timeline_start_frame - round(seconds_cursor * fps)) > 1
        ):
            raise ConflictError("剪辑主轨与当前导出片段区间不一致")
        seconds_cursor += duration
    if abs(video[-1].timeline_end_frame - round(seconds_cursor * fps)) > 1:
        raise ConflictError("剪辑主轨与当前导出总时长不一致")

    subtitle = [clip for clip in document.clips if clip.track == "subtitle"]
    subtitle_source = snapshot.get("subtitle_source") or []
    if len(subtitle) != len(subtitle_source):
        raise ConflictError("剪辑字幕与当前导出字幕数量不一致")
    for clip, cue in zip(subtitle, subtitle_source, strict=True):
        if (
            clip.text != cue["text"]
            or abs(clip.timeline_start_frame - round(float(cue["start_time"]) * fps)) > 1
            or abs(clip.timeline_end_frame - round(float(cue["end_time"]) * fps)) > 1
        ):
            raise ConflictError("剪辑字幕与当前导出字幕时间不一致")

    audio = {clip.clip_id: clip for clip in document.clips if clip.track in {"bgm", "dialogue", "ambience", "sfx"}}
    expected_audio: list[tuple[str, dict[str, Any]]] = []
    background_id = production_settings.get("background_audio_media_id")
    if background_id is not None:
        expected_audio.append(("bgm:episode", {
            "track": "bgm", "audio_media_id": background_id,
            "start_time": 0, "end_time": seconds_cursor,
            "gain": production_settings.get("background_audio_volume", 0.3),
            "fill": "loop", "mode": None, "confirmed": False,
        }))
    dialogue = [cue for cue in snapshot.get("dialogue_cues", []) if cue.get("audio_media_id") is not None]
    for index, cue in sorted(enumerate(dialogue), key=lambda row: float(row[1]["start_time"])):
        expected_audio.append((f"dialogue:{index}", {
            "track": "dialogue", **cue, "fill": "silence",
            "mode": cue.get("audio_mode", "replace"),
            "confirmed": cue.get("native_dialogue_mix_confirmed", False),
        }))
    for index, cue in sorted(enumerate(snapshot.get("sound_cues") or []), key=lambda row: float(row[1]["start_time"])):
        expected_audio.append((f"sound:{index}", {
            "track": cue["kind"], **cue, "fill": "loop" if cue.get("loop") else "silence",
            "mode": None, "confirmed": False,
        }))
    if set(audio) != {clip_id for clip_id, _ in expected_audio}:
        raise ConflictError("剪辑声音轨与当前导出声音数量不一致")
    for clip_id, cue in expected_audio:
        clip = audio[clip_id]
        if (
            clip.track != cue["track"]
            or clip.media_file_id != cue["audio_media_id"]
            or clip.audio_fill != cue["fill"]
            or clip.native_audio_mode != cue["mode"]
            or clip.native_mix_confirmed != bool(cue["confirmed"])
            or clip.gain != float(cue.get("gain", 1))
            or clip.muted
            or abs(clip.timeline_start_frame - round(float(cue["start_time"]) * fps)) > 1
            or abs(clip.timeline_end_frame - round(float(cue["end_time"]) * fps)) > 1
        ):
            raise ConflictError("剪辑声音轨与当前导出声音区间或策略不一致")


async def build_legacy_edit_projection(
    session: AsyncSession, *, episode_id: int, owner_id: int,
) -> tuple[EditDocument, dict[int, int]]:
    """Map the current E5 snapshot without writing a plan, draft, or media metadata."""
    episode = await session.get(Episode, episode_id)
    if episode is None or not await same_team(session, owner_id, episode.owner_id):
        raise NotFoundError("分集不存在")
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode_id))
    plan = await session.get(EpisodeProductionPlan, production.active_plan_id) if production and production.active_plan_id else None
    if plan is None or plan.episode_id != episode_id:
        raise ConflictError("当前分集没有活动片段计划")
    segments = list((await session.scalars(
        select(VideoSegment).where(VideoSegment.plan_id == plan.id, VideoSegment.episode_id == episode_id)
        .order_by(VideoSegment.order)
    )).all())
    if not segments:
        raise ConflictError("当前片段计划为空")
    versions = list((await session.scalars(
        select(SegmentVideoVersion).where(
            SegmentVideoVersion.segment_id.in_([segment.id for segment in segments]),
            SegmentVideoVersion.is_final.is_(True),
        )
    )).all())
    by_segment = {}
    for version in versions:
        if version.segment_id in by_segment:
            raise ConflictError("片段存在多个采用视频版本，请先核对")
        by_segment[version.segment_id] = version
    if len(by_segment) != len(segments):
        raise ConflictError("片段尚未全部采用视频版本")
    snapshot = await export_snapshot(session, episode, segments, by_segment)
    production_settings = production.settings or {}
    frame_rate = int(snapshot["output_spec"]["frame_rate"])
    source_frames = {}
    media_evidence = []
    storage_root = settings.storage_path.resolve()
    dialogue_audio = [cue for cue in snapshot.get("dialogue_cues", []) if cue.get("audio_media_id") is not None]
    sound_audio = snapshot.get("sound_cues") or []
    background_id = production_settings.get("background_audio_media_id")
    media_kinds = {version.media_file_id: "video" for version in versions}
    for media_id in [
        *([background_id] if background_id is not None else []),
        *(cue["audio_media_id"] for cue in dialogue_audio),
        *(cue["audio_media_id"] for cue in sound_audio),
    ]:
        if type(media_id) is not int or media_id <= 0 or media_kinds.get(media_id) == "video":
            raise ValidationError("声音素材标识无效或与视频素材冲突")
        media_kinds[media_id] = "audio"
    for media_id, kind in sorted(media_kinds.items()):
        media = await session.get(MediaFile, media_id)
        if media is None or media.kind != kind or not await same_team(session, owner_id, media.owner_id):
            raise ValidationError("剪辑素材不存在、类型错误或无权使用")
        if media.project_id != episode.project_id:
            linked = await session.scalar(select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == media_id,
            ))
            if linked is None:
                raise ValidationError("剪辑素材未加入当前项目")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ValidationError("剪辑素材文件不存在或路径越界")
        metadata = await probe_media_file(path, kind)
        duration = metadata.get("duration")
        if (
            (kind == "video" and (not metadata.get("width") or not metadata.get("height")))
            or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0
        ):
            raise ValidationError("剪辑素材无法解码或没有有效时长")
        frames = round(duration * frame_rate)
        if frames <= 0:
            raise ValidationError("剪辑素材没有可编辑帧")
        source_frames[media_id] = frames
        stat = path.stat()
        media_evidence.append({
            "id": media_id, "kind": kind, "hash": media.hash, "path": media.file_path,
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "duration": duration, "frames": frames,
        })

    clips = []
    segment_start_seconds = {}
    segment_video = {}
    cursor_frame = 0
    cursor_seconds = 0.0
    for segment in segments:
        version = by_segment[segment.id]
        source_in = round(float(segment.trim_in) * frame_rate)
        source_out = round((float(segment.trim_in) + float(segment.timeline_duration)) * frame_rate)
        clip_id = f"segment:{segment.id}"
        clips.append({
            "clip_id": clip_id, "track": "video", "lane": 0,
            "timeline_start_frame": cursor_frame,
            "source_in_frame": source_in, "source_out_frame": source_out,
            "media_file_id": version.media_file_id, "video_version_id": version.id,
        })
        segment_start_seconds[segment.id] = cursor_seconds
        segment_video[segment.id] = (clip_id, cursor_frame, source_out - source_in)
        cursor_frame += source_out - source_in
        cursor_seconds += float(segment.timeline_duration)

    lane_ends = [0] * 16
    for index, cue in enumerate(snapshot.get("subtitle_source") or []):
        segment_id = cue["segment_id"]
        if segment_id not in segment_video:
            raise ValidationError("字幕引用了非当前计划片段")
        anchor_id, video_start, video_length = segment_video[segment_id]
        offset = round((float(cue["start_time"]) - segment_start_seconds[segment_id]) * frame_rate)
        length = round((float(cue["end_time"]) - float(cue["start_time"])) * frame_rate)
        if offset < 0 or length <= 0 or offset + length > video_length:
            raise ConflictError("现有字幕时间无法安全映射到帧，请先核对片段时间")
        start = video_start + offset
        lane = next((i for i, end in enumerate(lane_ends) if end <= start), None)
        if lane is None:
            raise ConflictError("字幕重叠超过当前剪辑合同支持的轨道层数")
        lane_ends[lane] = start + length
        clips.append({
            "clip_id": f"subtitle:{segment_id}:{index}", "track": "subtitle", "lane": lane,
            "timeline_start_frame": start, "source_in_frame": 0, "source_out_frame": length,
            "text": cue["text"], "anchor_clip_id": anchor_id, "anchor_offset_frames": offset,
        })

    audio_lane_ends = {track: [0] * 16 for track in ("dialogue", "ambience", "sfx")}

    def add_audio_clip(*, track, clip_id, media_id, start_seconds, end_seconds, gain, fill,
                       segment_id=None, native_mode=None, native_confirmed=False):
        start = round(float(start_seconds) * frame_rate)
        length = round((float(end_seconds) - float(start_seconds)) * frame_rate)
        if start < 0 or length <= 0 or start + length > cursor_frame:
            raise ConflictError("声音条目时间无法安全映射到整集帧范围")
        anchor = {}
        if segment_id is not None:
            if segment_id not in segment_video:
                raise ValidationError("对白引用了非当前计划片段")
            anchor_id, video_start, video_length = segment_video[segment_id]
            offset = round((float(start_seconds) - segment_start_seconds[segment_id]) * frame_rate)
            start = video_start + offset
            if offset < 0 or offset + length > video_length:
                raise ConflictError("对白时间无法安全锚定所属片段")
            anchor = {"anchor_clip_id": anchor_id, "anchor_offset_frames": offset}
        lanes = audio_lane_ends.get(track)
        lane = next((i for i, end in enumerate(lanes) if end <= start), None) if lanes is not None else 0
        if lane is None:
            raise ConflictError("同类声音重叠超过当前轨道层数")
        if lanes is not None:
            lanes[lane] = start + length
        clips.append({
            "clip_id": clip_id, "track": track, "lane": lane,
            "timeline_start_frame": start, "source_in_frame": 0, "source_out_frame": length,
            "media_file_id": media_id, "gain": float(gain), "audio_fill": fill,
            "native_audio_mode": native_mode, "native_mix_confirmed": bool(native_confirmed),
            **anchor,
        })

    if background_id is not None:
        add_audio_clip(
            track="bgm", clip_id="bgm:episode", media_id=background_id,
            start_seconds=0, end_seconds=cursor_frame / frame_rate,
            gain=production_settings.get("background_audio_volume", 0.3), fill="loop",
        )
    for index, cue in sorted(enumerate(dialogue_audio), key=lambda row: float(row[1]["start_time"])):
        add_audio_clip(
            track="dialogue", clip_id=f"dialogue:{index}", media_id=int(cue["audio_media_id"]),
            start_seconds=cue["start_time"], end_seconds=cue["end_time"], gain=cue.get("gain", 1),
            fill="silence", segment_id=cue["segment_id"],
            native_mode=cue.get("audio_mode", "replace"),
            native_confirmed=cue.get("native_dialogue_mix_confirmed", False),
        )
    for index, cue in sorted(enumerate(sound_audio), key=lambda row: float(row[1]["start_time"])):
        track = str(cue.get("kind"))
        if track not in {"ambience", "sfx"} or (track == "sfx" and cue.get("loop")):
            raise ValidationError("声音条目类型或循环方式不受支持")
        add_audio_clip(
            track=track, clip_id=f"sound:{index}", media_id=int(cue["audio_media_id"]),
            start_seconds=cue["start_time"], end_seconds=cue["end_time"], gain=cue.get("gain", 1),
            fill="loop" if cue.get("loop") else "silence",
        )

    evidence = {"snapshot": snapshot, "media": media_evidence, "settings": production_settings}
    source_fingerprint = sha256(json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    document = validate_document({
        "episode_id": episode_id, "plan_id": plan.id, "plan_revision": plan.revision,
        "production_revision": production.revision, "source_fingerprint": source_fingerprint,
        "frame_rate": frame_rate, "revision": 0, "clips": clips,
    }, source_frames)
    assert_legacy_export_parity(document, snapshot, production_settings)
    return document, source_frames


async def verify_edit_sources(
    session: AsyncSession, *, document: EditDocument, owner_id: int,
) -> dict[int, int]:
    """Recheck the live source before an internal save; never accept client frame limits."""
    baseline, frames = await build_legacy_edit_projection(
        session, episode_id=document.episode_id, owner_id=owner_id,
    )
    if (
        document.plan_id != baseline.plan_id
        or document.plan_revision != baseline.plan_revision
        or document.production_revision != baseline.production_revision
        or document.source_fingerprint != baseline.source_fingerprint
        or document.frame_rate != baseline.frame_rate
    ):
        raise ConflictError("剪辑来源已变化，请重新核对当前制作计划及素材")
    allowed = {
        (clip.media_file_id, clip.video_version_id)
        for clip in baseline.clips if clip.track == "video"
    }
    if any(
        (clip.media_file_id, clip.video_version_id) not in allowed
        for clip in document.clips if clip.track == "video"
    ):
        raise ValidationError("剪辑引用了非当前计划采用的视频版本")
    allowed_audio = {
        (clip.track, clip.media_file_id, clip.audio_fill, clip.native_audio_mode, clip.native_mix_confirmed)
        for clip in baseline.clips if clip.track in {"bgm", "dialogue", "ambience", "sfx"}
    }
    if any(
        (clip.track, clip.media_file_id, clip.audio_fill, clip.native_audio_mode, clip.native_mix_confirmed)
        not in allowed_audio
        for clip in document.clips if clip.track in {"bgm", "dialogue", "ambience", "sfx"}
    ):
        raise ValidationError("声音轨引用或原声处理策略与当前制作计划不一致")
    return frames
