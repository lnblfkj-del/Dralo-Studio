"""Stable compatibility exports for media services."""

from app.core.config import settings
from app.services.media_core_service import (
    MEDIA_RULES, classify_upload, ensure_media_metadata, get_owned_media,
    link_media_to_project, list_media, probe_media_file, to_media_out,
    validate_decodable_media,
)
from app.services.media_delete_service import delete_files, delete_media
from app.services.media_export_service import (
    _episode_audio_arguments, _episode_dialogue_audio_arguments,
    _has_audio_stream, _normalize_episode_segment, _segment_normalization_command,
    _srt_timestamp, export_episode_video,
)
from app.services.media_history_service import list_generation_history
from app.services import media_core_service as _media_core
from app.services import media_processing_service as _media_processing


def _sync_media_probe_patch() -> None:
    """Keep legacy monkeypatches of media_service.probe_media_file effective."""
    _media_core.probe_media_file = probe_media_file
    _media_processing.probe_media_file = probe_media_file


async def get_video_thumbnail(*args, **kwargs):
    return await _media_processing.get_video_thumbnail(*args, **kwargs)


async def ensure_segment_last_frame(*args, **kwargs):
    _sync_media_probe_patch()
    return await _media_processing.ensure_segment_last_frame(*args, **kwargs)


async def save_upload(*args, **kwargs):
    _sync_media_probe_patch()
    return await _media_processing.save_upload(*args, **kwargs)


async def finalize_video_job(*args, **kwargs):
    _sync_media_probe_patch()
    return await _media_processing.finalize_video_job(*args, **kwargs)

__all__ = [
    "MEDIA_RULES", "settings", "classify_upload", "probe_media_file",
    "ensure_media_metadata", "validate_decodable_media", "get_video_thumbnail",
    "ensure_segment_last_frame", "save_upload", "finalize_video_job",
    "_srt_timestamp", "_has_audio_stream", "_episode_audio_arguments",
    "_episode_dialogue_audio_arguments", "_segment_normalization_command",
    "_normalize_episode_segment", "export_episode_video", "list_media",
    "to_media_out", "get_owned_media", "link_media_to_project", "delete_media",
    "delete_files", "list_generation_history",
]
