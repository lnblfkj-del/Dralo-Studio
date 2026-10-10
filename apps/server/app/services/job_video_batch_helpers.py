"""Shared validation and media-input helpers for video job creation."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import EpisodeProductionPlan, SegmentVideoVersion, VideoSegment


def _enforce_cost_limit(
    pricing: dict[str, Any], max_cost_cents: int, *, label: str
) -> int:
    estimated = pricing.get("estimated_cents")
    if type(estimated) is not int:
        raise ConflictError(f"{label}费用未知，不能在没有明确费用上限的情况下提交")
    if estimated > max_cost_cents:
        raise ConflictError(
            f"{label}最新预估费用为 {estimated / 100:.2f} 元，超过已确认上限 "
            f"{max_cost_cents / 100:.2f} 元；请重新预检并确认"
        )
    return estimated


def _binding_role(binding: dict[str, Any]) -> str:
    role = binding.get("role") or binding.get("usage_type")
    key = str(binding.get("adoption_key") or "")
    if role in {"first_frame", "last_frame"}:
        return role
    if key in {"first_frame", "frame:first_frame"} or key.startswith("first_frame:"):
        return "first_frame"
    if key in {"last_frame", "frame:last_frame"} or key.startswith("last_frame:"):
        return "last_frame"
    return "reference_image"


def _compile_segment_media(
    refs: dict[str, Any], effective: dict[str, Any]
) -> tuple[int | None, int | None, list[int], list[dict[str, Any]]]:
    bindings = [item for item in refs.get("asset_bindings", []) if isinstance(item, dict)]
    frames: dict[str, list[int]] = {"first_frame": [], "last_frame": []}
    for binding in bindings:
        role = _binding_role(binding)
        if role in frames and type(binding.get("media_file_id")) is int:
            frames[role].append(binding["media_file_id"])
    if any(len(set(values)) > 1 for values in frames.values()):
        raise ConflictError("片段的首帧和尾帧各只能采用一个资产版本")

    compiled_frames: dict[str, int | None] = {}
    for role in ("first_frame", "last_frame"):
        adopted = frames[role][0] if frames[role] else None
        submitted = effective.pop(f"{role}_media_id", None)
        if adopted is not None and submitted not in (None, adopted):
            raise ConflictError(f"片段参数中的 {role} 与当前采用资产版本不一致")
        compiled_frames[role] = adopted or submitted

    reference_media_ids = list(
        dict.fromkeys(
            int(value)
            for value in refs.get("reference_media_ids", [])
            if type(value) is int and value > 0
        )
    )
    reference_media_ids = [
        media_id
        for media_id in reference_media_ids
        if media_id not in {compiled_frames["first_frame"], compiled_frames["last_frame"]}
    ]
    return (
        compiled_frames["first_frame"],
        compiled_frames["last_frame"],
        reference_media_ids,
        bindings,
    )


async def _resolve_continuity_input(
    session: AsyncSession,
    segment: VideoSegment,
    refs: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    continuity = refs.get("continuity")
    if not isinstance(continuity, dict):
        return None, None
    source_key = str(continuity.get("source_lineage_key") or "").strip()
    if not source_key:
        return None, None
    source = await session.scalar(
        select(VideoSegment).where(
            VideoSegment.plan_id == segment.plan_id,
            VideoSegment.lineage_key == source_key,
        )
    )
    if source is None or source.order >= segment.order:
        raise ConflictError("连续帧依赖的前序片段已变化，请重新保存片段计划")
    version = await session.scalar(
        select(SegmentVideoVersion)
        .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
        .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
        .where(
            VideoSegment.episode_id == segment.episode_id,
            VideoSegment.lineage_key == source.lineage_key,
            VideoSegment.plan_id == segment.plan_id,
            SegmentVideoVersion.is_final.is_(True),
        )
        .order_by(EpisodeProductionPlan.version.desc(), SegmentVideoVersion.version.desc())
    )
    if version is None:
        raise ConflictError(
            f"前序片段 {source.order:02d} 尚未采用视频，当前片段不能使用其尾帧"
        )
    from app.services.segment_video_candidate_service import candidate_evidence
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    evidence = await candidate_evidence(session, segment=source, plan=plan, version=version)
    if evidence["status"] != "adopted":
        raise ConflictError("前序片段的采用视频已失效，请重新生成并采用后再使用尾帧")
    from app.services.media_service import ensure_segment_last_frame

    frame = await ensure_segment_last_frame(session, version)
    return frame.id, {
        "source_segment_id": version.segment_id,
        "active_source_segment_id": source.id,
        "source_segment_lineage_key": source.lineage_key,
        "source_segment_order": source.order,
        "source_video_version_id": version.id,
        "source_video_media_id": version.media_file_id,
        "derived_last_frame_media_id": frame.id,
        "target_role": "first_frame",
    }


def _sound_input_summary(
    bindings: list[dict[str, Any]],
    parameters: dict[str, Any],
    voice_guidance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sounds = [
        {
            "asset_id": item.get("asset_id"),
            "asset_version_id": item.get("asset_version_id"),
            "media_file_id": item.get("media_file_id"),
            "role": item.get("role") or item.get("usage_type"),
        }
        for item in bindings
        if item.get("media_kind") == "audio"
        or item.get("role") in {"audio_reference", "voice_reference"}
    ]
    policy = parameters.get("audio_policy") or {}
    capability = policy.get("capability") or {}
    return {
        "bindings": sounds,
        "delivery": "postproduction_evidence" if sounds else "none",
        "native_audio_generation": bool(
            capability.get("output") == "always_on" or parameters.get("generate_audio") or parameters.get("audio")
        ),
        "native_audio_status": capability.get("output", "legacy"),
        "audio_policy": policy,
        "voice_guidance": voice_guidance or {
            "schema_version": "segment_voice_guidance.v1",
            "entries": [],
            "prompt_text": "",
        },
    }
