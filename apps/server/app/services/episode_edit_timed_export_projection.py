"""Offline subtitle and audio projection from the frame-based edit contract."""

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.services.episode_edit_contract import AUDIO_TRACKS, EditDocument, validate_document


def _us(frame: int, frame_rate: int) -> int:
    return round(frame * 1_000_000 / frame_rate)


def _audio_chunks(clip: Any, source_length: int) -> list[tuple[int, int, int]]:
    """Return target start, source start and length in frames; gaps mean silence."""
    remaining = clip.duration_frames
    target = clip.timeline_start_frame
    source = clip.source_in_frame
    chunks = []
    while remaining > 0:
        length = min(remaining, source_length - source)
        if length <= 0:
            break
        chunks.append((target, source, length))
        target += length
        remaining -= length
        if clip.audio_fill != "loop":
            break
        source = 0
    return chunks


def project_timed_export_rows(
    document: EditDocument | dict,
    *,
    source_frames: dict[int, int],
    paths_by_media: dict[int, str],
) -> dict[str, Any]:
    """Build exporter inputs and explicit fidelity blockers; no production dispatch."""
    document = validate_document(document, source_frames)
    fps = document.frame_rate
    episode_frames = max(
        (clip.timeline_end_frame for clip in document.clips if clip.track == "video"), default=0
    )
    subtitles = []
    jianying_subtitles = []
    audio = []
    premiere_audio = []
    jianying_audio = []
    jianying_native_mute_ranges = []
    blockers: dict[str, list[str]] = {"ffmpeg": [], "premiere": [], "jianying": []}
    clips = sorted(
        (clip for clip in document.clips if clip.track != "video"),
        key=lambda clip: (clip.timeline_start_frame, clip.track, clip.lane, clip.clip_id),
    )
    for clip in clips:
        start = clip.timeline_start_frame
        end = clip.timeline_end_frame
        if clip.track == "subtitle":
            subtitles.append(
                {
                    "clip_id": clip.clip_id,
                    "text": clip.text,
                    "start_frame": start,
                    "end_frame": end,
                    "start_time": start / fps,
                    "end_time": end / fps,
                }
            )
            jianying_subtitles.append(
                {
                    "text": clip.text,
                    "start_us": _us(start, fps),
                    "end_us": _us(end, fps),
                }
            )
            continue
        if clip.track not in AUDIO_TRACKS:
            raise ValidationError("剪辑包含不支持的轨道")
        media_id = clip.media_file_id
        path = paths_by_media.get(media_id)
        if not path or not Path(path).name:
            raise ValidationError("剪辑声音缺少已核验的导出素材路径")
        gain = 0.0 if clip.muted else clip.gain
        audio.append(
            {
                "clip_id": clip.clip_id,
                "track": clip.track,
                "media_file_id": media_id,
                "path": path,
                "start_frame": start,
                "end_frame": end,
                "start_time": start / fps,
                "end_time": end / fps,
                "source_in_frame": clip.source_in_frame,
                "source_in_time": clip.source_in_frame / fps,
                "gain": gain,
                "audio_fill": clip.audio_fill,
                "fade_in_frames": clip.fade_in_frames,
                "fade_out_frames": clip.fade_out_frames,
                "fade_in_time": clip.fade_in_frames / fps,
                "fade_out_time": clip.fade_out_frames / fps,
                "audio_mode": clip.native_audio_mode,
                "native_mix_confirmed": clip.native_mix_confirmed,
            }
        )
        if clip.track == "dialogue" and clip.native_audio_mode == "replace":
            blockers["premiere"].append(f"{clip.clip_id}:native_replace")
            jianying_native_mute_ranges.append(
                {
                    "start_us": _us(start, fps),
                    "end_us": _us(end, fps),
                    "source": clip.clip_id,
                }
            )
        if gain != 1:
            blockers["premiere"].append(f"{clip.clip_id}:gain_or_mute")
        if clip.fade_in_frames or clip.fade_out_frames:
            for exporter in blockers:
                blockers[exporter].append(f"{clip.clip_id}:audio_fades_require_envelope_renderer")
        for part, (target, source, length) in enumerate(
            _audio_chunks(clip, source_frames[media_id])
        ):
            premiere_audio.append(
                {
                    "name": f"{clip.clip_id}:{part}",
                    "role": clip.track,
                    "path": path,
                    "media_file_id": media_id,
                    "start_frame": target,
                    "end_frame": target + length,
                    "in_frame": source,
                    "out_frame": source + length,
                    "source_duration_frames": source_frames[media_id],
                }
            )
            jianying_audio.append(
                {
                    "name": clip.clip_id,
                    "role": clip.track,
                    "draft_path": path,
                    "start_us": _us(target, fps),
                    "source_start_us": _us(source, fps),
                    "duration_us": _us(target + length, fps) - _us(target, fps),
                    "source_duration_us": _us(source_frames[media_id], fps),
                    "gain": gain,
                }
            )
    bgm = [item for item in audio if item["track"] == "bgm"]
    if bgm and (
        len(bgm) != 1
        or bgm[0]["start_frame"] != 0
        or bgm[0]["end_frame"] != episode_frames
        or bgm[0]["source_in_frame"] != 0
    ):
        blockers["ffmpeg"].append("bgm:current_mixer_requires_full_length_from_source_zero")
    return {
        "episode_id": getattr(document, "episode_id", None),
        "revision": document.revision,
        "frame_rate": fps,
        "duration_frames": episode_frames,
        "subtitles": subtitles,
        "jianying_subtitles": jianying_subtitles,
        "audio": audio,
        "premiere_audio": premiere_audio,
        "jianying_audio": jianying_audio,
        "jianying_native_mute_ranges": jianying_native_mute_ranges,
        "blockers_by_export": blockers,
    }
