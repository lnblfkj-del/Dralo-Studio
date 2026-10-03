"""Immutable production plan creation and manual plan construction."""

import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.models import (
    Episode,
    EpisodeProductionPlan,
    ProviderModel,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.services.asset_binding_service import binding_role
from app.services.segment_plan_core_service import (
    _episode_scenes,
    _episode_shots,
    _production,
    _validated_refs,
)
from app.services.segment_plan_read_service import serialize_plan
from app.services.segment_script_semantics import (
    AI_SOURCE_TYPES,
    STRUCTURED_SCRIPT_KEY,
    normalize_segment,
)
from app.services.video_model_contract import choose_duration, validate_parameters, validate_segment
from app.services.video_model_contract import normalize as normalize_video_model


async def create_plan(
    session: AsyncSession,
    episode: Episode,
    *,
    expected_production_revision: int,
    provider_model_id: int | None,
    model_capability_snapshot: dict[str, Any],
    parameters: dict[str, Any],
    status: str,
    segments: list[dict[str, Any]],
    source_type: str = "manual",
    parent_plan_id: int | None = None,
    excluded_shot_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create and activate an immutable plan version; never creates generation jobs."""
    production = await _production(session, episode)
    if production.revision != expected_production_revision:
        raise ConflictError("制作配置已变化，请刷新后重新规划")
    if episode.finalized_script_revision != episode.script_revision:
        raise ConflictError("正式剧本未确认或已变化，不能创建片段计划")
    if parent_plan_id is None:
        parent_plan_id = production.active_plan_id
    parent_plan = None
    if parent_plan_id is not None:
        parent_plan = await session.get(EpisodeProductionPlan, parent_plan_id)
        if parent_plan is None or parent_plan.episode_id != episode.id:
            raise ValidationError("父片段计划不属于当前分集")
    capability_contract: dict[str, Any] | None = None
    if provider_model_id is not None:
        model = await session.get(ProviderModel, provider_model_id)
        if model is None or not model.enabled or model.model_type != "video":
            raise ConflictError("视频模型不存在、已停用或类型不正确")
        capability_contract = normalize_video_model(model)
        validate_parameters(capability_contract, parameters)
        # Capabilities are always captured server-side.  A caller cannot weaken
        # the current contract by submitting a fabricated snapshot.
        model_capability_snapshot = capability_contract

    shots = await _episode_shots(session, episode.id)
    available = {shot.id: shot for shot in shots}
    scene_rows = await _episode_scenes(session, episode.id)
    semantic_scenes = {
        scene.id: {
            "scene_id": scene.id,
            "name": scene.name,
            "location": scene.location,
            "time_of_day": scene.time_of_day,
            "description": scene.description,
        }
        for scene in scene_rows
    }
    semantic_shots = {
        shot.id: {
            "shot_id": shot.id,
            "scene_id": shot.scene_id,
            "shot_size": shot.shot_size,
            "camera_angle": shot.camera_angle,
            "camera_movement": shot.camera_movement,
            "action": shot.action,
            "dialogue": shot.dialogue,
            "audio_note": shot.audio_note,
        }
        for shot in shots
    }
    submitted = [shot_id for item in segments for shot_id in item["shot_ids"]]
    # Only lifecycle operations can change the exclusion ledger. Ordinary saves
    # inherit it from the parent, never from client-supplied parameters.
    excluded = set(excluded_shot_ids if excluded_shot_ids is not None else
                   (parent_plan.parameters or {}).get("excluded_shot_ids", []) if parent_plan else [])
    excluded -= set(submitted)
    parameters = {**parameters, "excluded_shot_ids": sorted(excluded)}
    if status == "confirmed" and excluded and parameters.get("confirmed_excluded_shot_ids") != sorted(excluded):
        raise ValidationError("时间轴已移除来源镜头，请先确认正文删减范围再生产")
    if excluded_shot_ids is None:
        parameters.pop("timeline_selected_lineage_key", None)
    if len(submitted) != len(set(submitted)):
        raise ValidationError("一条分镜只能归属一个视频片段")
    if set(submitted) | excluded != set(available):
        raise ValidationError("片段计划必须且只能覆盖本集全部分镜")

    normalized: list[dict[str, Any]] = []
    for item in segments:
        validated_refs = await _validated_refs(session, episode, item.get("refs") or {})
        manual_insert = not item["shot_ids"] and (item.get("parameters") or {}).get("manual_insert") is True
        if not item["shot_ids"] and not manual_insert:
            raise ValidationError("空分镜片段必须来自手动插入")
        timeline = (float(item.get("timeline_duration") or item["generation_duration"]) if manual_insert else
                    sum(float(available[shot_id].duration or 4) for shot_id in item["shot_ids"]))
        requested_timeline = item.get("timeline_duration")
        if requested_timeline is not None:
            timeline = float(requested_timeline)
        generation = float(item["generation_duration"])
        times = [generation, timeline, float(item.get("trim_in", 0)), float(item.get("trim_out", 0))]
        if any(not math.isfinite(value) or value < 0 for value in times) or generation == 0 or timeline == 0:
            raise ValidationError("片段时间必须是有限秒数，生成和采用时长必须大于零")
        if item.get("trim_in", 0) + timeline + item.get("trim_out", 0) > generation + 0.05:
            raise ValidationError("裁切区间超出模型生成时长")
        if capability_contract is not None:
            item_parameters = {**parameters, **(item.get("parameters") or {})}
            from app.services.job_video_batch_helpers import _compile_segment_media

            # Keep frame-identity validation while deferring image budgets to
            # video preflight; saving a script must not discard its asset links.
            _compile_segment_media(validated_refs, dict(item_parameters))
            validate_parameters(capability_contract, item_parameters)
            if item_parameters.get("duration") not in (None, generation):
                raise ValidationError("片段参数 duration 必须与生成时长一致")
            validate_segment(
                capability_contract,
                generation_duration=generation,
                shot_count=max(1, len(item["shot_ids"])),
                timeline_duration=timeline,
            )
        normalized_item = normalize_segment(
            item,
            list(item["shot_ids"]),
            semantic_shots,
            semantic_scenes,
            force_structure=(source_type in AI_SOURCE_TYPES and not manual_insert
                             and not (source_type in {"split", "merge", "add"} and str(item.get("prompt") or "").strip())),
        )
        cameras = ((normalized_item.get("parameters") or {}).get("structured_script") or {}).get("camera", [])
        try:
            explicit_durations = {entry["shot_id"]: float(entry["duration"]) for entry in cameras if "duration" in entry}
        except (TypeError, ValueError, KeyError) as exc:
            raise ValidationError("镜头时长必须为有效秒数") from exc
        if explicit_durations and (
            set(explicit_durations) != set(item["shot_ids"])
            or any(not math.isfinite(value) or value <= 0 for value in explicit_durations.values())
            or abs(sum(explicit_durations.values()) - timeline) > 0.05
        ):
            raise ValidationError("镜头时长必须为正数，且合计等于片段时长")
        normalized.append({
            **normalized_item,
            "shot_durations": explicit_durations,
            "lineage_key": str(item.get("lineage_key") or uuid4()),
            "parent_lineage_keys": [
                str(value) for value in item.get("parent_lineage_keys") or []
            ],
            "timeline_duration": timeline,
            "refs": validated_refs,
        })

    if status == "confirmed" and not normalized:
        raise ValidationError("空片段计划只能保存为草稿")
    if status == "confirmed" and any((item.get("parameters") or {}).get("text_split_review_required") for item in normalized):
        raise ValidationError("纯文本拆分后请先核对各片段正文范围，再确认生产")
    if status == "confirmed" and any(
        not str(item.get("prompt") or "").strip() for item in normalized
    ):
        raise ValidationError("确认片段计划前必须补全每个片段的提示词")
    if status == "confirmed" and any((item.get("refs") or {}).get("unmatched_assets") for item in normalized):
        raise ValidationError("请先处理片段中未匹配的资产，再确认生产计划")

    lineage_keys = [item["lineage_key"] for item in normalized]
    if len(lineage_keys) != len(set(lineage_keys)):
        raise ValidationError("同一计划中的片段稳定标识不能重复")
    foreign_lineage = await session.scalar(
        select(VideoSegment.id).where(
            VideoSegment.lineage_key.in_(lineage_keys),
            VideoSegment.episode_id != episode.id,
        ).limit(1)
    )
    if foreign_lineage is not None:
        raise ValidationError("片段稳定标识已属于其他分集")
    lineage_order = {
        item["lineage_key"]: order
        for order, item in enumerate(normalized, start=1)
    }
    for order, item in enumerate(normalized, start=1):
        continuity = (item.get("refs") or {}).get("continuity")
        if not continuity:
            continue
        source_key = continuity["source_lineage_key"]
        source_order = lineage_order.get(source_key)
        if source_order is None:
            raise ValidationError("片段连续帧依赖的来源片段不存在")
        if source_order >= order:
            raise ValidationError("片段连续帧只能依赖当前计划中的前序片段")
        if any(
            binding_role(binding) == "first_frame"
            for binding in (item.get("refs") or {}).get("asset_bindings", [])
        ):
            raise ValidationError("使用前序片段尾帧时不能同时绑定手工首帧")

    version = (
        int(
            (
                await session.scalar(
                    select(func.coalesce(func.max(EpisodeProductionPlan.version), 0)).where(
                        EpisodeProductionPlan.episode_id == episode.id
                    )
                )
            )
            or 0
        )
        + 1
    )
    if production.active_plan_id is not None:
        await session.execute(
            update(EpisodeProductionPlan)
            .where(EpisodeProductionPlan.id == production.active_plan_id)
            .values(status="superseded")
        )
    plan = EpisodeProductionPlan(
        episode_id=episode.id,
        owner_id=episode.owner_id,
        version=version,
        source_type=source_type,
        parent_plan_id=parent_plan_id,
        status=status,
        source_script_revision=episode.script_revision,
        provider_model_id=provider_model_id,
        model_capability_snapshot=model_capability_snapshot,
        parameters=parameters,
        total_timeline_duration=sum(item["timeline_duration"] for item in normalized),
        total_generation_duration=sum(item["generation_duration"] for item in normalized),
        confirmed_at=datetime.now(UTC) if status == "confirmed" else None,
    )
    session.add(plan)
    await session.flush()
    production.active_plan_id = plan.id
    production.revision += 1
    production.source_script_revision = episode.script_revision
    production.script_stale = False
    production.script_stale_reason = None
    for segment_order, item in enumerate(normalized, start=1):
        segment = VideoSegment(
            plan_id=plan.id,
            episode_id=episode.id,
            order=segment_order,
            lineage_key=item["lineage_key"],
            parent_lineage_keys=item["parent_lineage_keys"],
            title=item.get("title") or f"片段 {segment_order:02d}",
            generation_duration=item["generation_duration"],
            timeline_duration=item["timeline_duration"],
            trim_in=item.get("trim_in", 0),
            trim_out=item.get("trim_out", 0),
            prompt=item["prompt"],
            negative_prompt=item.get("negative_prompt"),
            parameters=item.get("parameters") or {},
            refs=item.get("refs") or {},
            status=item.get("segment_status") or "pending",
        )
        session.add(segment)
        await session.flush()
        cursor = 0.0
        source_duration = sum(float(available[shot_id].duration or 4) for shot_id in item["shot_ids"])
        explicit_durations = item["shot_durations"]
        for shot_order, shot_id in enumerate(item["shot_ids"], start=1):
            duration = explicit_durations.get(shot_id, float(available[shot_id].duration or 4) * item["timeline_duration"] / source_duration)
            session.add(
                VideoSegmentShot(
                    segment_id=segment.id,
                    shot_id=shot_id,
                    order=shot_order,
                    start_time=cursor,
                    end_time=cursor + duration,
                )
            )
            cursor += duration
    await session.flush()
    from app.services.production_snapshot_service import capture_script
    saved_segments = (await session.scalars(select(VideoSegment).where(VideoSegment.plan_id == plan.id))).all()
    for saved_segment in saved_segments:
        await capture_script(session, saved_segment)
    from app.services.segment_candidate_reuse_service import carry_candidates
    await carry_candidates(session, plan, saved_segments)
    return await serialize_plan(session, plan)


def _plan_input_segment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "lineage_key": item["lineage_key"],
        "parent_lineage_keys": item.get("parent_lineage_keys") or [],
        "title": item.get("title"),
        "shot_ids": [link["shot_id"] for link in item.get("shots", [])],
        "generation_duration": item["generation_duration"],
        "timeline_duration": item["timeline_duration"],
        "trim_in": item.get("trim_in", 0),
        "trim_out": item.get("trim_out", 0),
        "prompt": item["prompt"],
        "negative_prompt": item.get("negative_prompt"),
        "parameters": item.get("parameters") or {},
        "refs": item.get("refs") or {},
        "segment_status": item.get("status") or "pending",
    }


def _without_structured_script(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in parameters.items()
        if key != STRUCTURED_SCRIPT_KEY
    }


async def create_manual_plan(
    session: AsyncSession,
    episode: Episode,
    *,
    expected_production_revision: int,
    provider_model_id: int | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Create an editable blank plan from shots without creating any job."""
    shots = await _episode_shots(session, episode.id)
    if not shots:
        raise ConflictError("请先完成场景与分镜拆解，再新建手写片段脚本")
    contract: dict[str, Any] | None = None
    if provider_model_id is not None:
        model = await session.get(ProviderModel, provider_model_id)
        if model is None or not model.enabled or model.model_type != "video":
            raise ConflictError("视频模型不存在、已停用或类型不正确")
        contract = normalize_video_model(model)
        validate_parameters(contract, parameters)

    total = sum(float(shot.duration or 4) for shot in shots)
    target_count = max(1, min(len(shots), round(total / 8)))
    target_duration = total / target_count
    max_duration = max(contract["durations"]) if contract else 30.0
    max_shots = int(contract["max_shots_per_segment"]) if contract else 20
    groups: list[list[Shot]] = []
    current: list[Shot] = []
    current_duration = 0.0
    for shot in shots:
        duration = float(shot.duration or 4)
        scene_changed = bool(current and current[-1].scene_id != shot.scene_id)
        would_overflow = current_duration + duration > max_duration + 0.05
        reached_target = bool(current and current_duration >= target_duration - 0.05)
        if current and (
            scene_changed or would_overflow or len(current) >= max_shots or reached_target
        ):
            groups.append(current)
            current = []
            current_duration = 0.0
        current.append(shot)
        current_duration += duration
    if current:
        groups.append(current)

    segment_inputs: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        timeline = sum(float(shot.duration or 4) for shot in group)
        generation = choose_duration(contract, timeline) if contract else timeline
        segment_inputs.append({
            "title": f"片段 {index:02d}",
            "shot_ids": [shot.id for shot in group],
            "generation_duration": generation,
            "timeline_duration": timeline,
            "trim_in": 0,
            "trim_out": generation - timeline,
            "prompt": "",
            "negative_prompt": None,
            "parameters": {},
            "refs": {},
        })
    return await create_plan(
        session,
        episode,
        expected_production_revision=expected_production_revision,
        provider_model_id=provider_model_id,
        model_capability_snapshot=contract or {},
        parameters=parameters,
        status="draft",
        source_type="manual",
        segments=segment_inputs,
    )
