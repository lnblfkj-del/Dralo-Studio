"""Offline video-track projection for the three existing episode exporters."""

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.services.episode_edit_contract import EditDocument, validate_document


def _microseconds(frame: int, frame_rate: int) -> int:
    return round(frame * 1_000_000 / frame_rate)


def project_video_export_rows(
    document: EditDocument | dict,
    *,
    source_frames: dict[int, int],
    paths_by_media: dict[int, str],
) -> dict[str, Any]:
    """Compile verified frame edits without submitting or changing a production export."""
    document = validate_document(document, source_frames)
    frame_rate = document.frame_rate
    ffmpeg_rows = []
    premiere_rows = []
    jianying_rows = []
    video = sorted(
        (clip for clip in document.clips if clip.track == "video"),
        key=lambda clip: clip.timeline_start_frame,
    )
    for clip in video:
        media_id = clip.media_file_id
        path = paths_by_media.get(media_id)
        if not path or not Path(path).name:
            raise ValidationError("剪辑视频缺少已核验的导出素材路径")
        source_length = source_frames[media_id]
        start = clip.timeline_start_frame
        end = clip.timeline_end_frame
        start_us = _microseconds(start, frame_rate)
        end_us = _microseconds(end, frame_rate)
        in_us = _microseconds(clip.source_in_frame, frame_rate)
        ffmpeg_rows.append({
            "clip_id": clip.clip_id,
            "media_file_id": media_id,
            "video_version_id": clip.video_version_id,
            "source": path,
            "trim_in": clip.source_in_frame / frame_rate,
            "timeline_duration": clip.duration_frames / frame_rate,
            "start_frame": start,
            "end_frame": end,
        })
        premiere_rows.append({
            "name": clip.clip_id,
            "path": path,
            "start_frame": start,
            "end_frame": end,
            "in_frame": clip.source_in_frame,
            "out_frame": clip.source_out_frame,
            "source_duration_frames": source_length,
        })
        jianying_rows.append({
            "draft_path": path,
            "duration_us": end_us - start_us,
            "trim_in_us": in_us,
            "source_duration_us": _microseconds(source_length, frame_rate),
        })
    return {
        "episode_id": getattr(document, "episode_id", None),
        "revision": document.revision,
        "frame_rate": frame_rate,
        "duration_frames": video[-1].timeline_end_frame if video else 0,
        "ffmpeg": ffmpeg_rows,
        "premiere": premiere_rows,
        "jianying": jianying_rows,
    }
