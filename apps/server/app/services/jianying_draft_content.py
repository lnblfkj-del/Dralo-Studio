"""Jianying compatibility metadata and plaintext draft timeline builder."""

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from app.services.jianying_diagnostics import (
    DRAFT_PATH_TOKEN,
    DRAFT_ROOT_TOKEN,
    INSTALLER_FILENAME,
    MEDIA_ROOT_TOKEN,
    REFERENCE_VALIDATION,
    RENAME_WARNING,
    TARGET_APP,
    TARGET_VERSION,
)
from app.services.jianying_installer import _id

def compatibility_metadata(
    installed_draft_root: Path | str,
    *,
    stable_media_root: Path | str | None = None,
) -> dict[str, Any]:
    return {
        "target_app": TARGET_APP,
        "target_version": TARGET_VERSION,
        "format": "Jianying plaintext standard draft",
        "isolated_directory": True,
        "modifies_jianying_index": False,
        "portable_paths": False,
        "rename_safe": False,
        "rename_safe_after_install": True,
        "installer_required": True,
        "installer": INSTALLER_FILENAME,
        "required_install_path": str(installed_draft_root),
        "stable_media_path": str(stable_media_root or MEDIA_ROOT_TOKEN),
        "path_tokens_resolved_by_installer": [
            MEDIA_ROOT_TOKEN,
            DRAFT_ROOT_TOKEN,
            DRAFT_PATH_TOKEN,
        ],
        "package_checksum_scope": (
            "checksums.sha256 validates the downloaded package before installation; "
            "the installer then rewrites installed JSON path tokens."
        ),
        "structural_validation": True,
        "artifact_application_validation": "not_run",
        "reference_application_validation": REFERENCE_VALIDATION,
        "supported": [
            "segment_order",
            "source_trim",
            "canvas_dimensions",
            "native_segment_audio",
            "embedded_media",
            "background_music",
            "dialogue_audio",
            "native_dialogue_replace_mute",
            "ambience_audio",
            "sound_effects",
            "editable_subtitle_text",
        ],
        "limitations": [
            "剪映11.4新保存草稿会加密JSON; 本包使用该版本仍保留兼容读取的明文标准草稿结构。",
            "对白replace模式会切分视频时间轴并把对应区间音量设为0; 结构已验证, 仍需在剪映中实机核对。",
            "字幕仅保存基础白字黑描边样式, 不复现平台专属模板和动画。",
            "必须解压后运行安装脚本; 安装器拒绝覆盖已有剪映草稿或稳定媒体目录。",
            "安装器会把媒体复制到stable_media_path并把草稿JSON令牌改写为本机绝对路径。",
            RENAME_WARNING,
            "参考验收已通过, 但每个新生成包仍应在目标机器执行素材在线与重开检查。",
        ],
    }


def build_draft_content(
    *,
    draft_id: str,
    width: int,
    height: int,
    segments: list[dict[str, Any]],
    audio_clips: list[dict[str, Any]] | None = None,
    subtitles: list[dict[str, Any]] | None = None,
    native_mute_ranges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the plaintext standard draft timeline retained by Jianying 11.4."""
    materials: dict[str, list[dict[str, Any]]] = {
        "videos": [],
        "canvases": [],
        "placeholder_infos": [],
        "speeds": [],
        "sound_channel_mappings": [],
        "material_colors": [],
        "vocal_separations": [],
        "audios": [],
        "audio_fades": [],
        "beats": [],
        "texts": [],
    }
    merged_mute_ranges: list[tuple[int, int]] = []
    for item in sorted(
        native_mute_ranges or [],
        key=lambda value: int(value.get("start_us") or 0),
    ):
        start = max(int(item.get("start_us") or 0), 0)
        end = max(int(item.get("end_us") or 0), start)
        if end <= start:
            continue
        if merged_mute_ranges and start <= merged_mute_ranges[-1][1]:
            previous_start, previous_end = merged_mute_ranges[-1]
            merged_mute_ranges[-1] = (previous_start, max(previous_end, end))
        else:
            merged_mute_ranges.append((start, end))
    track_segments = []
    cursor = 0
    for item in segments:
        material_id, speed_id, canvas_id, sound_id = _id(), _id(), _id(), _id()
        duration = int(item["duration_us"])
        trim_in = int(item.get("trim_in_us") or 0)
        materials["videos"].append(
            {
                "id": material_id,
                "type": "video",
                "duration": int(item["source_duration_us"]),
                "path": item["draft_path"],
                "width": width,
                "height": height,
                "material_id": "",
                "crop": {},
                "stable": {"time_range": {}},
                "matting": {"path": ""},
                "check_flag": 62978047,
                "video_algorithm": {
                    "path": "",
                    "story_video_modify_video_config": {},
                },
                "is_copyright": True,
                "smart_match_info": {"type": 0},
                "beauty_face_auto_preset": {},
                "video_mask_stroke": {"resource_id": "", "path": "", "type": ""},
                "video_mask_shadow": {"resource_id": "", "path": ""},
            }
        )
        materials["speeds"].append({"id": speed_id, "type": "speed"})
        materials["canvases"].append(
            {"id": canvas_id, "type": "canvas_blur", "blur": 0.0}
        )
        materials["sound_channel_mappings"].append(
            {"id": sound_id, "type": "none"}
        )
        segment_end = cursor + duration
        boundaries = {cursor, segment_end}
        for mute_start, mute_end in merged_mute_ranges:
            if mute_end <= cursor or mute_start >= segment_end:
                continue
            boundaries.add(max(mute_start, cursor))
            boundaries.add(min(mute_end, segment_end))
        ordered_boundaries = sorted(boundaries)
        for piece_start, piece_end in pairwise(ordered_boundaries):
            piece_duration = piece_end - piece_start
            source_start = trim_in + piece_start - cursor
            source_range = {"duration": piece_duration}
            if source_start:
                source_range["start"] = source_start
            target_range = {"duration": piece_duration}
            if piece_start:
                target_range["start"] = piece_start
            muted = any(
                mute_start <= piece_start and piece_end <= mute_end
                for mute_start, mute_end in merged_mute_ranges
            )
            track_segment = {
                "id": _id(),
                "source_timerange": source_range,
                "target_timerange": target_range,
                "render_timerange": {},
                "clip": {
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": 0.0, "y": 0.0},
                    "flip": {},
                },
                "uniform_scale": {},
                "material_id": material_id,
                "extra_material_refs": [speed_id, canvas_id, sound_id],
                "hdr_settings": {"mode": 1},
                "responsive_layout": {},
                "source": "segmentsourcenormal",
            }
            if merged_mute_ranges:
                track_segment["volume"] = 0.0 if muted else 1.0
                track_segment["last_nonzero_volume"] = 1.0
            track_segments.append(track_segment)
        cursor += duration
    tracks: list[dict[str, Any]] = [
        {"id": _id(), "type": "video", "segments": track_segments}
    ]
    for item in audio_clips or []:
        material_id, fade_id, beat_id = _id(), _id(), _id()
        duration = int(item["duration_us"])
        source_duration = int(item["source_duration_us"])
        materials["audios"].append(
            {
                "id": material_id,
                "type": "extract_music",
                "duration": source_duration,
                "path": item["draft_path"],
                "name": item.get("name") or "audio",
                "check_flag": 1,
                "source_platform": 0,
                "team_id": "",
                "wave_points": [],
            }
        )
        materials["audio_fades"].append(
            {"id": fade_id, "type": "audio_fade", "fade_in_duration": 0, "fade_out_duration": 0}
        )
        materials["beats"].append(
            {
                "id": beat_id,
                "type": "beats",
                "ai_beats": {"beat_speed_infos": [], "beats_path": ""},
                "gear": 0,
                "gear_count": 0,
                "mode": 0,
                "user_beats": [],
                "user_delete_ai_beats": None,
            }
        )
        tracks.append(
            {
                "id": _id(),
                "type": "audio",
                "name": str(item.get("role") or "audio"),
                "segments": [
                    {
                        "id": _id(),
                        "material_id": material_id,
                        "source_timerange": {
                            "start": int(item.get("source_start_us") or 0),
                            "duration": duration,
                        },
                        "target_timerange": {
                            "start": int(item.get("start_us") or 0),
                            "duration": duration,
                        },
                        "extra_material_refs": [fade_id, beat_id],
                        "speed": 1.0,
                        "volume": float(item.get("gain", 1.0)),
                        "last_nonzero_volume": float(item.get("gain", 1.0)),
                        "visible": True,
                        "source": "segmentsourcenormal",
                    }
                ],
            }
        )
    for item in subtitles or []:
        material_id = _id()
        text = str(item.get("text") or "").strip()
        content = json.dumps(
            {
                "text": text,
                "styles": [
                    {
                        "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}}},
                        "font": {"id": "", "path": ""},
                        "range": [0, len(text)],
                        "size": 8.0,
                        "strokes": [
                            {
                                "content": {"solid": {"color": [0.0, 0.0, 0.0]}},
                                "width": 0.08,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        materials["texts"].append(
            {
                "id": material_id,
                "type": "text",
                "content": content,
                "alignment": 1,
                "font_size": 8.0,
                "font_path": "",
                "font_id": "",
                "text_color": "#FFFFFF",
                "background_alpha": 0.0,
                "bold_width": 0.0,
                "italic_degree": 0,
                "letter_spacing": 0.0,
                "line_spacing": 0.02,
            }
        )
        duration = int(item["end_us"]) - int(item["start_us"])
        tracks.append(
            {
                "id": _id(),
                "type": "text",
                "name": "subtitle",
                "segments": [
                    {
                        "id": _id(),
                        "material_id": material_id,
                        "source_timerange": {"start": 0, "duration": duration},
                        "target_timerange": {
                            "start": int(item["start_us"]),
                            "duration": duration,
                        },
                        "clip": {
                            "scale": {"x": 1.0, "y": 1.0},
                            "transform": {"x": 0.0, "y": -0.8},
                            "rotation": 0.0,
                        },
                        "extra_material_refs": [],
                        "visible": True,
                        "source": "segmentsourcenormal",
                    }
                ],
            }
        )
    platform = {
        "app_id": 3704,
        "app_source": "lv",
        "app_version": TARGET_VERSION,
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": "windows",
        "os_version": "",
    }
    return {
        "id": draft_id,
        "version": 360000,
        "new_version": "110400",
        "duration": cursor,
        "config": {},
        "canvas_config": {"ratio": "custom", "width": width, "height": height},
        "tracks": tracks,
        "materials": materials,
        "keyframes": {
            "adjusts": [],
            "audios": [],
            "filters": [],
            "handwrites": [],
            "stickers": [],
            "texts": [],
            "videos": [],
        },
        "platform": platform,
        "last_modified_platform": platform,
        "render_index_track_mode_on": True,
        "path": "",
        "uneven_animation_template_info": {},
        "smart_ads_info": {},
        "function_assistant_info": {},
    }


