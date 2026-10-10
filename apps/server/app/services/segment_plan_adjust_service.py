"""Segment lifecycle, structural adjustments and continuity reports."""

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Episode, ProviderModel
from app.services.segment_plan_core_service import _episode_shots, get_active_plan
from app.services.segment_plan_write_service import (
    _plan_input_segment,
    create_plan,
)
from app.services.segment_script_edit_service import (
    merge_parameters,
    merge_refs,
    needs_exclusion_review,
    split_parameters,
)
from app.services.segment_script_semantics import continuity_issues as semantic_continuity_issues
from app.services.video_model_contract import choose_duration, validate_segment
from app.services.video_model_contract import normalize as normalize_video_model


async def change_segment_lifecycle(
    session: AsyncSession,
    episode: Episode,
    *,
    operation: str,
    expected_production_revision: int,
    segment_id: int,
    shot_ids: list[int],
    title: str | None,
) -> dict[str, Any]:
    """Create a new immutable plan version for manual segment lifecycle changes."""
    current = await get_active_plan(session, episode)
    if operation in {"insert_before", "insert_after", "delete"}:
        from app.services.segment_timeline_edit_service import edit_timeline
        return await edit_timeline(
            session, episode, current, operation=operation, segment_id=segment_id,
            title=title, expected_production_revision=expected_production_revision,
        )
    items = [_plan_input_segment(item) for item in current["segments"]]
    index = next(
        (position for position, item in enumerate(current["segments"]) if item["id"] == segment_id),
        None,
    )
    if index is None:
        raise NotFoundError("要操作的片段不属于当前活动计划")
    selected = items[index]
    if operation == "add":
        current_shots = selected["shot_ids"]
        if not shot_ids or not current_shots:
            raise ValidationError("拆出片段需要选择已有分镜；空白片段请使用插入")
        if not set(shot_ids).issubset(current_shots):
            raise ValidationError("新增片段只能使用原片段中的分镜")
        positions = [current_shots.index(shot_id) for shot_id in shot_ids]
        if positions != list(range(min(positions), max(positions) + 1)):
            raise ValidationError("新增片段必须移动原片段中连续的分镜")
        if len(shot_ids) == len(current_shots):
            raise ValidationError("新增片段不能移走原片段全部分镜")
        remaining = [shot_id for shot_id in current_shots if shot_id not in set(shot_ids)]
        if remaining != current_shots[:len(remaining)] and remaining != current_shots[-len(remaining):]:
            raise ValidationError("移动分镜后原片段必须仍保持连续")
        shot_map = {shot.id: shot for shot in await _episode_shots(session, episode.id)}
        model = (
            await session.get(ProviderModel, current["provider_model_id"])
            if current["provider_model_id"] is not None
            else None
        )
        contract = normalize_video_model(model) if model is not None else None

        def rebuilt(base: dict[str, Any], ids: list[int], label: str) -> dict[str, Any]:
            timing = {link["shot_id"]: link["end_time"] - link["start_time"] for link in current["segments"][index]["shots"]}
            timeline = sum(timing.get(shot_id, float(shot_map[shot_id].duration or 4)) for shot_id in ids)
            generation = choose_duration(contract, timeline) if contract else timeline
            return {
                **base,
                "lineage_key": str(uuid4()),
                "parent_lineage_keys": [selected["lineage_key"]],
                "title": label,
                "shot_ids": ids,
                "generation_duration": generation,
                "timeline_duration": timeline,
                "trim_in": 0,
                "trim_out": generation - timeline,
                "parameters": split_parameters(base, ids),
                "segment_status": "pending",
            }

        original = rebuilt(selected, remaining, selected.get("title") or "原片段")
        added = rebuilt(
            selected,
            shot_ids,
            (title or "").strip() or f"{selected.get('title') or '片段'} 新增",
        )
        insert_after = positions[0] > 0
        items[index:index + 1] = [original, added] if insert_after else [added, original]
    elif operation == "copy":
        copied = {
            **selected,
            "lineage_key": str(uuid4()),
            "parent_lineage_keys": [selected["lineage_key"]],
            "title": (title or "").strip() or f"{selected.get('title') or '片段'} 副本",
            "segment_status": "pending",
        }
        items[index] = copied
    elif operation == "archive":
        if selected.get("segment_status") == "archived":
            raise ConflictError("片段已经归档")
        items[index] = {**selected, "segment_status": "archived"}
    elif operation == "restore":
        if selected.get("segment_status") != "archived":
            raise ConflictError("只有归档片段可以恢复")
        items[index] = {**selected, "segment_status": "pending"}
    else:
        raise ValidationError("不支持的片段生命周期操作")

    return await create_plan(
        session,
        episode,
        expected_production_revision=expected_production_revision,
        provider_model_id=current["provider_model_id"],
        model_capability_snapshot=current.get("model_capability_snapshot") or {},
        parameters={
            **(current.get("parameters") or {}),
            "source": "segment_lifecycle",
            "operation": operation,
            "parent_plan_id": current["id"],
        },
        status=(
            "draft"
            if any(item.get("segment_status") == "archived" or (item.get("parameters") or {}).get("text_split_review_required") for item in items) or needs_exclusion_review(current)
            else "confirmed"
            if all(str(item.get("prompt") or "").strip() for item in items)
            else "draft"
        ),
        segments=items,
        source_type=operation,
        parent_plan_id=current["id"],
    )


async def adjust_plan(
    session: AsyncSession,
    episode: Episode,
    *,
    operation: str,
    expected_production_revision: int,
    segment_ids: list[int],
    after_shot_id: int | None,
    ordered_segment_ids: list[int],
) -> dict[str, Any]:
    """Apply a deterministic split/merge/reorder and create a new plan version."""
    current = await get_active_plan(session, episode)
    if current["provider_model_id"] is None and operation != "reorder":
        raise ConflictError("当前计划没有视频模型能力合同，不能自动调整")
    model = await session.get(ProviderModel, current["provider_model_id"]) if current["provider_model_id"] else None
    if operation != "reorder" and (model is None or not model.enabled or model.model_type != "video"):
        raise ConflictError("当前计划的视频模型不可用")
    contract = normalize_video_model(model) if model else current.get("model_capability_snapshot") or {}
    items = [_plan_input_segment(item) for item in current["segments"]]
    id_to_index = {item["id"]: index for index, item in enumerate(current["segments"])}

    if operation == "split":
        segment_id = segment_ids[0]
        index = id_to_index.get(segment_id)
        if index is None:
            raise NotFoundError("要拆分的片段不存在")
        shot_ids = items[index]["shot_ids"]
        if after_shot_id not in shot_ids or shot_ids.index(after_shot_id) >= len(shot_ids) - 1:
            raise ValidationError("拆分位置必须位于片段内部")
        cut = shot_ids.index(after_shot_id) + 1
        shot_map = {shot.id: shot for shot in await _episode_shots(session, episode.id)}
        pieces = []
        for suffix, ids in zip(("A", "B"), (shot_ids[:cut], shot_ids[cut:]), strict=True):
            timing = {link["shot_id"]: link["end_time"] - link["start_time"] for link in current["segments"][index]["shots"]}
            timeline = sum(timing[shot_id] for shot_id in ids)
            piece_parameters = split_parameters(items[index], ids)
            pieces.append({
                **items[index],
                "lineage_key": str(uuid4()),
                "parent_lineage_keys": [items[index]["lineage_key"]],
                "title": f"{items[index].get('title') or '片段'} {suffix}",
                "shot_ids": ids,
                "generation_duration": choose_duration(contract, timeline),
                "timeline_duration": timeline,
                "trim_in": 0,
                "trim_out": choose_duration(contract, timeline) - timeline,
                "parameters": piece_parameters,
            })
        items[index:index + 1] = pieces
    elif operation == "merge":
        indices = [id_to_index.get(segment_id) for segment_id in segment_ids]
        if any(index is None for index in indices):
            raise NotFoundError("要合并的片段不存在")
        normalized_indices = [int(index) for index in indices if index is not None]
        if normalized_indices != list(range(min(normalized_indices), max(normalized_indices) + 1)):
            raise ValidationError("只能合并相邻且按顺序选择的片段")
        selected = items[min(normalized_indices):max(normalized_indices) + 1]
        if any(not item["shot_ids"] for item in selected):
            raise ValidationError("独立手写片段暂不支持按分镜合并，请分别编辑正文")
        merged_shots = [shot_id for item in selected for shot_id in item["shot_ids"]]
        shot_map = {shot.id: shot for shot in await _episode_shots(session, episode.id)}
        scene_ids = {shot_map[shot_id].scene_id for shot_id in merged_shots}
        if len(scene_ids) != 1:
            raise ValidationError("不同场景的片段不能合并")
        timeline = sum(float(item["timeline_duration"]) for item in selected)
        generation = choose_duration(contract, timeline)
        validate_segment(contract, generation_duration=generation, shot_count=len(merged_shots), timeline_duration=timeline)
        merged = {
            **selected[0],
            "lineage_key": str(uuid4()),
            "parent_lineage_keys": [item["lineage_key"] for item in selected],
            "title": " + ".join(item.get("title") or "片段" for item in selected),
            "shot_ids": merged_shots,
            "generation_duration": generation,
            "timeline_duration": timeline,
            "trim_in": 0,
            "trim_out": generation - timeline,
            "prompt": "\n".join(item["prompt"] for item in selected),
            "negative_prompt": "；".join(filter(None, (item.get("negative_prompt") for item in selected))) or None,
            "parameters": merge_parameters(selected),
            "refs": merge_refs(selected),
        }
        items[min(normalized_indices):max(normalized_indices) + 1] = [merged]
    elif operation == "reorder":
        current_ids = [item["id"] for item in current["segments"]]
        if len(ordered_segment_ids) != len(set(ordered_segment_ids)) or set(ordered_segment_ids) != set(current_ids):
            raise ValidationError("重新编排必须且只能包含当前全部片段一次")
        items = [items[id_to_index[segment_id]] for segment_id in ordered_segment_ids]
    else:
        raise ValidationError("不支持的片段调整操作")

    return await create_plan(
        session,
        episode,
        expected_production_revision=expected_production_revision,
        provider_model_id=current["provider_model_id"],
        model_capability_snapshot=contract,
        parameters={
            **(current.get("parameters") or {}),
            "source": "director_adjustment",
            "parent_plan_id": current["id"],
            "operation": operation,
        },
        status="confirmed" if not needs_exclusion_review(current) and all(item.get("prompt", "").strip() and item.get("segment_status") != "archived" and not (item.get("parameters") or {}).get("text_split_review_required") for item in items) else "draft",
        segments=items,
        source_type=operation,
        parent_plan_id=current["id"],
        excluded_shot_ids=(current.get("parameters") or {}).get("excluded_shot_ids", []),
    )


async def continuity_report(session: AsyncSession, episode: Episode) -> dict[str, Any]:
    plan = await get_active_plan(session, episode)
    issues: list[dict[str, Any]] = []
    for segment in plan["segments"]:
        if segment.get("status") == "archived":
            issues.append({
                "code": "archived_segment",
                "severity": "blocking",
                "segment_id": segment["id"],
                "message": "片段已归档，恢复后才能进入视频生成",
            })
        if not str(segment.get("prompt") or "").strip():
            issues.append({
                "code": "empty_prompt",
                "severity": "blocking",
                "segment_id": segment["id"],
                "message": "片段提示词为空，不能进入视频生成",
            })
        unresolved = (segment.get("refs") or {}).get("unresolved_assets", [])
        unmatched = (segment.get("refs") or {}).get("unmatched_assets", [])
        if unmatched:
            issues.append({"code": "unmatched_asset", "severity": "blocking", "segment_id": segment["id"],
                           "message": "资产匹配待确认：" + "、".join(unmatched)})
        if unresolved:
            issues.append({
                "code": "unresolved_asset",
                "severity": "blocking",
                "segment_id": segment["id"],
                "message": "片段存在未绑定到最终图片版本的正式资产",
            })
        if segment["timeline_duration"] > segment["generation_duration"] + 0.05:
            issues.append({
                "code": "invalid_duration",
                "severity": "blocking",
                "segment_id": segment["id"],
                "message": "片段时间轴超过模型生成时长",
            })
    issues.extend(semantic_continuity_issues(plan["segments"]))
    status = "blocked" if any(item["severity"] == "blocking" for item in issues) else ("warning" if issues else "passed")
    return {"plan_id": plan["id"], "status": status, "issues": issues}
