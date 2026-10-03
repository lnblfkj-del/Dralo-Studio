"""Stable compatibility exports for segment production planning."""

from app.services.segment_plan_adjust_service import (
    adjust_plan,
    change_segment_lifecycle,
    continuity_report,
)
from app.services.segment_plan_core_service import (
    _episode_scenes,
    _episode_shots,
    _production,
    _validated_refs,
    get_active_plan,
    initialize_legacy_plan,
    mirror_legacy_shot_version,
    select_segment_video_version,
)
from app.services.segment_plan_read_service import serialize_plan
from app.services.segment_plan_write_service import (
    _plan_input_segment,
    _without_structured_script,
    create_manual_plan,
    create_plan,
)

__all__ = [
    "_production", "_episode_shots", "_episode_scenes", "_validated_refs",
    "get_active_plan", "select_segment_video_version", "initialize_legacy_plan",
    "mirror_legacy_shot_version", "create_plan", "_plan_input_segment",
    "_without_structured_script",
    "change_segment_lifecycle", "adjust_plan", "create_manual_plan",
    "continuity_report", "serialize_plan",
]
