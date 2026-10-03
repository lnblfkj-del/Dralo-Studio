"""Explicit timeline edits preserve source shots."""

from uuid import uuid4

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import ProviderModel
from app.services.segment_plan_core_service import _production
from app.services.segment_plan_write_service import _plan_input_segment, create_plan
from app.services.video_model_contract import normalize


async def edit_timeline(session, episode, current, *, operation, segment_id, title,
                        expected_production_revision):
    production = await _production(session, episode)
    if production.revision != expected_production_revision:
        raise ConflictError("制作配置已变化，请刷新后重新编辑时间轴")
    items = [_plan_input_segment(item) for item in current["segments"]]
    parameters = {**(current.get("parameters") or {})}
    excluded = set(parameters.get("excluded_shot_ids", []))
    model_id = current["provider_model_id"]
    index = next((i for i, item in enumerate(current["segments"]) if item["id"] == segment_id), None)
    if index is None and items:
        raise NotFoundError("要操作的片段不属于当前活动计划")
    if operation == "delete":
        if index is None:
            raise NotFoundError("要删除的片段不存在")
        removed = items[index]
        if any(((item.get("refs") or {}).get("continuity") or {}).get("source_lineage_key") == removed["lineage_key"] for item in items):
            raise ConflictError("其他片段引用了该片段尾帧，请先解除连续帧依赖")
        excluded.update(removed["shot_ids"])
        del items[index]
    elif operation in {"insert_before", "insert_after"}:
        if len(items) >= 100:
            raise ValidationError("每集最多100个片段")
        model = await session.get(ProviderModel, model_id) if model_id else None
        duration = float(min(normalize(model)["durations"])) if model else 5.0
        blank = {
            "lineage_key": str(uuid4()), "parent_lineage_keys": [],
            "title": (title or "").strip() or "新片段", "shot_ids": [],
            "generation_duration": duration, "timeline_duration": duration,
            "trim_in": 0, "trim_out": 0, "prompt": "", "negative_prompt": None,
            "parameters": {"manual_insert": True}, "refs": {}, "segment_status": "pending",
        }
        position = 0 if index is None else index + (operation == "insert_after")
        items.insert(position, blank)
        parameters["timeline_selected_lineage_key"] = blank["lineage_key"]
    else:
        raise ValidationError("不支持的时间轴操作")
    return await create_plan(
        session, episode, expected_production_revision=expected_production_revision,
        provider_model_id=model_id, model_capability_snapshot=current.get("model_capability_snapshot") or {},
        parameters={**parameters, "operation": operation}, status="draft", segments=items,
        source_type="manual", parent_plan_id=current["id"], excluded_shot_ids=sorted(excluded),
    )
