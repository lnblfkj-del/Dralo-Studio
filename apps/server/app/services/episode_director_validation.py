"""Validation and repair helpers for episode director model output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.models import Job
from app.schemas.episode_director import DirectorModelOutput
from app.services.episode_duration_policy import episode_duration_window
from app.services.segment_script_semantics import (
    STRUCTURED_SCRIPT_KEY,
    build_structured_script,
    compile_prompt,
)
from app.services.segment_script_semantics import (
    continuity_issues as semantic_continuity_issues,
)
from app.services.video_model_contract import (
    canonical_parameters,
    validate_parameters,
    validate_segment,
)

DIRECTOR_SCHEMA_VERSION = "episode_director_plan.v1"
TARGET_EPISODE_DIRECTOR = "episode_director_plan"


def _unresolved_visual_assets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Unbound audio uses the existing sound guidance path, not an image blocker.
    return [item for item in items if item.get("asset_type") != "voice"
            and item.get("role") not in {"first_frame", "last_frame"}]


def _segment_input(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "lineage_key": item.get("lineage_key"),
        "parent_lineage_keys": item.get("parent_lineage_keys") or [],
        "title": item.get("title"),
        "shot_ids": list(item.get("shot_ids") or [
            shot["shot_id"] for shot in item.get("shots") or []
        ]),
        "generation_duration": item["generation_duration"],
        "timeline_duration": item["timeline_duration"],
        "trim_in": item.get("trim_in", 0),
        "trim_out": item.get("trim_out", 0),
        "prompt": item.get("prompt") or "",
        "negative_prompt": item.get("negative_prompt"),
        "parameters": item.get("parameters") or {},
        "refs": item.get("refs") or {},
    }

def _json_text(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        first_break = value.find("\n")
        last_fence = value.rfind("```")
        value = value[first_break + 1:last_fence].strip() if first_break >= 0 and last_fence > first_break else value
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError("导演模型未返回有效 JSON") from exc
    if not isinstance(result, dict):
        raise ValidationError("导演模型输出必须是 JSON 对象")
    return result


def validate_model_result(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if payload.get("auto_prepare"):
        from app.services.episode_auto_planning_validation import validate_result
        return validate_result(payload, result)
    execution = payload.get("director_execution") or {}
    input_snapshot = execution.get("input") or {}
    capability = execution.get("video_model_capability_snapshot") or {}
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("导演模型没有返回规划内容")
    try:
        output = DirectorModelOutput.model_validate(_json_text(text))
    except PydanticValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)[:10]
        fields = ", ".join(".".join(map(str, error["loc"])) for error in errors)
        raise ValidationError(f"规划结果字段需要修正：{fields}", details={"errors": errors}) from exc

    mode = execution.get("planning_mode") or "replan_episode"
    input_shots = input_snapshot.get("shots") or []
    planning_shots = input_snapshot.get("planning_shots") or input_shots
    all_shot_order = [int(item["shot_id"]) for item in input_shots]
    shot_order = [int(item["shot_id"]) for item in planning_shots]
    input_by_id = {int(item["shot_id"]): item for item in input_shots}
    proposed_by_id = {item.shot_id: item for item in output.shots}
    if len(proposed_by_id) != len(output.shots) or set(proposed_by_id) != set(shot_order):
        raise ValidationError("导演提案必须且只能覆盖本次目标分镜一次")
    for shot_id, proposed in proposed_by_id.items():
        source = input_by_id[shot_id]
        source_dialogue = str(source.get("dialogue") or "").strip()
        source_audio = str(source.get("audio_note") or "").strip()
        if source_dialogue and proposed.dialogue.strip() != source_dialogue:
            raise ValidationError("导演提案必须原样保留来源台词，不能遗漏或改写")
        if source_audio and proposed.audio_note.strip() != source_audio:
            raise ValidationError("导演提案必须原样保留来源配乐、环境声和音效说明")
    target_duration = (
        float(input_snapshot.get("target_duration") or 0)
        if mode == "replan_episode"
        else sum(float(item.get("duration") or 0) for item in planning_shots)
    )
    planned_duration = sum(item.duration for item in output.shots)
    if mode != "replan_episode" and abs(planned_duration - target_duration) > 0.05:
        raise ValidationError(
            f"分镜总时长 {planned_duration:g} 秒与本次目标 {target_duration:g} 秒不一致"
        )
    duration_issue = None
    lower, upper = episode_duration_window(target_duration) if target_duration > 0 else (0, 0)
    if mode == "replan_episode" and target_duration > 0 and not lower <= planned_duration <= upper:
        duration_issue = {
            "code": "episode_duration_variance",
            "severity": "warning",
            "message": (
                f"分镜计划约 {planned_duration:g} 秒，超出本集目标区间 {lower:g}-{upper:g} 秒；"
                "请核对对白表演和镜头节奏，不会自动删改正文。"
            ),
        }

    submitted = [shot_id for segment in output.segments for shot_id in segment.shot_ids]
    if len(submitted) != len(set(submitted)) or set(submitted) != set(shot_order):
        raise ValidationError("视频片段必须且只能覆盖本次目标分镜一次")
    positions = {shot_id: index for index, shot_id in enumerate(shot_order)}
    bindings = input_snapshot.get("asset_bindings") or []
    current_plan = input_snapshot.get("current_plan") or None
    current_segments = current_plan.get("segments") or [] if current_plan else []
    target_ids = input_snapshot.get("target_segment_ids") or []
    target_segments = [item for item in current_segments if item["id"] in set(target_ids)]
    if mode != "replan_episode":
        if len(output.segments) != len(target_segments):
            raise ValidationError("局部AI必须逐个返回每个目标片段")
        for proposed, current in zip(output.segments, target_segments, strict=True):
            if proposed.shot_ids != current["shot_ids"]:
                raise ValidationError("局部AI不能改变目标片段的分镜范围或顺序")

    generated_segments: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    from app.schemas.director_response_protocol import response_contract
    contract = response_contract("segment")
    model_proposed_states = (payload.get("response_protocol") == contract
                             or (payload.get("pipeline_version") == 5
                                 and (payload.get("response_protocols") or {}).get("segment") == contract))
    for index, segment in enumerate(output.segments, start=1):
        indices = [positions[shot_id] for shot_id in segment.shot_ids]
        if indices != list(range(min(indices), max(indices) + 1)):
            raise ValidationError("片段只能组合输入顺序中连续的分镜")
        scene_ids = {input_by_id[shot_id]["scene_id"] for shot_id in segment.shot_ids}
        if len(scene_ids) != 1:
            raise ValidationError("一个片段不能跨越不同场景")
        timeline = sum(proposed_by_id[shot_id].duration for shot_id in segment.shot_ids)
        current_target = target_segments[index - 1] if mode != "replan_episode" else None
        if current_target is not None:
            current_refs = current_target.get("refs") or {}
            resolved = list(current_refs.get("asset_bindings") or [])
            unresolved = list(current_refs.get("unresolved_assets") or [])
        else:
            relevant = [
                item for item in bindings if item.get("shot_id") in (None, *segment.shot_ids)
            ]
            resolved = [item for item in relevant if item.get("resolved")]
            unresolved = [item for item in relevant if not item.get("resolved")]
        unique_media = list(dict.fromkeys(
            int(item["media_file_id"])
            for item in resolved
            if item.get("media_kind") == "image"
        ))
        compiled_parameters = canonical_parameters(capability, {
            **((current_target or {}).get("parameters") or {}),
            **segment.parameters,
            **execution.get("parameters", {}),
        })
        semantic_shots = {
            shot_id: {
                **input_by_id[shot_id],
                **proposed_by_id[shot_id].model_dump(),
            }
            for shot_id in segment.shot_ids
        }
        semantic_scenes = {
            int(input_by_id[shot_id]["scene_id"]): {
                "scene_id": int(input_by_id[shot_id]["scene_id"]),
                "name": input_by_id[shot_id].get("scene_name"),
                "location": input_by_id[shot_id].get("scene_location"),
                "time_of_day": input_by_id[shot_id].get("scene_time_of_day"),
                "description": input_by_id[shot_id].get("scene_description"),
            }
            for shot_id in segment.shot_ids
        }
        structured_script = build_structured_script(
            segment.shot_ids,
            semantic_shots,
            semantic_scenes,
            entry_state=segment.entry_state,
            exit_state=segment.exit_state,
            model_proposed_states=model_proposed_states,
        )
        compiled_parameters[STRUCTURED_SCRIPT_KEY] = structured_script
        if current_target is not None:
            previous_script = (current_target.get("parameters") or {}).get(STRUCTURED_SCRIPT_KEY) or {}
            # A local AI pass must not undo an explicitly authored dialogue revision.
            previous_by_shot = {}
            for line in previous_script.get("dialogue", []):
                previous_by_shot.setdefault(line["shot_id"], []).append(line)
            for shot_id, lines in previous_by_shot.items():
                if (any(line.get("text_source") == "manual" or line.get("speaker_source") == "manual" for line in lines)
                        and len(lines) != sum(line["shot_id"] == shot_id for line in structured_script["dialogue"])):
                    raise ValidationError("手写台词布局与当前来源不同，请先核对，不会自动改写或丢弃手写内容")
            positions = {}
            for index, line in enumerate(structured_script["dialogue"]):
                shot_id = line["shot_id"]
                position = positions.get(shot_id, 0)
                positions[shot_id] = position + 1
                previous = previous_by_shot.get(shot_id, [])
                if position < len(previous):
                    authored = previous[position]
                    if authored.get("text_source") == "manual" or authored.get("speaker_source") == "manual":
                        structured_script["dialogue"][index] = authored
            if previous_script.get("scene_source") == "manual":
                structured_script["scene"] = previous_script["scene"]
                structured_script["scene_source"] = "manual"
        validate_parameters(capability, compiled_parameters)
        if compiled_parameters.get("duration") not in (None, segment.generation_duration):
            raise ValidationError("片段参数 duration 必须与 generation_duration 一致")
        validate_segment(
            capability,
            generation_duration=segment.generation_duration,
            shot_count=len(segment.shot_ids),
            timeline_duration=timeline,
        )
        visual_unresolved = _unresolved_visual_assets(unresolved)
        if visual_unresolved:
            blocking.append(
                {
                    "code": "unresolved_asset",
                    "segment_order": index,
                    "message": "片段存在未绑定到最终图片版本的正式资产",
                    "asset_ids": sorted({int(item["asset_id"]) for item in visual_unresolved}),
                }
            )
        parent_lineages = []
        if current_target is not None:
            lineage_key = current_target["lineage_key"]
            parent_lineages = current_target.get("parent_lineage_keys") or []
        else:
            lineage_key = None
            proposed_shots = set(segment.shot_ids)
            parent_lineages = [
                item["lineage_key"]
                for item in current_segments
                if proposed_shots.intersection(item["shot_ids"])
            ]
        generated_segments.append(
            {
                "lineage_key": lineage_key,
                "parent_lineage_keys": parent_lineages,
                "title": segment.title,
                "shot_ids": segment.shot_ids,
                "generation_duration": segment.generation_duration,
                "timeline_duration": timeline,
                "trim_in": 0,
                "trim_out": max(0.0, segment.generation_duration - timeline),
                "prompt": compile_prompt(structured_script),
                "negative_prompt": segment.negative_prompt or None,
                "parameters": compiled_parameters,
                "refs": {
                    "asset_bindings": resolved,
                    "reference_media_ids": unique_media,
                    "unresolved_assets": unresolved,
                },
            }
        )
    ordered_from_segments = [
        shot_id for segment in generated_segments for shot_id in segment["shot_ids"]
    ]
    if ordered_from_segments != shot_order:
        raise ValidationError("片段顺序必须保持本次目标分镜顺序")

    if mode == "replan_episode":
        segments = generated_segments
    else:
        generated_by_id = {
            target["id"]: generated
            for target, generated in zip(target_segments, generated_segments, strict=True)
        }
        segments = [
            generated_by_id.get(item["id"], _segment_input(item)) for item in current_segments
        ]
    full_order = [shot_id for segment in segments for shot_id in segment["shot_ids"]]
    if full_order != all_shot_order:
        raise ValidationError("局部AI合并结果改变了非目标片段或全片分镜顺序")
    for index, segment in enumerate(segments, start=1):
        unresolved = _unresolved_visual_assets((segment.get("refs") or {}).get("unresolved_assets") or [])
        if unresolved and not any(
            item.get("code") == "unresolved_asset" and item.get("segment_order") == index
            for item in blocking
        ):
            blocking.append({
                "code": "unresolved_asset",
                "segment_order": index,
                "message": "片段存在未绑定到最终图片版本的正式资产",
                "asset_ids": sorted({int(item["asset_id"]) for item in unresolved}),
            })
        if not str(segment.get("prompt") or "").strip():
            blocking.append({
                "code": "empty_prompt",
                "segment_order": index,
                "message": "片段提示词仍为空",
            })
    semantic_segments = segments
    if mode == "optimize_segment":
        semantic_segments = [
            {**segment, "id": current["id"]}
            for segment, current in zip(segments, current_segments, strict=True)
        ]
    semantic_issues = semantic_continuity_issues(semantic_segments)
    if mode == "optimize_segment":
        # A local edit may affect its two boundaries, but it must not become
        # responsible for unchanged dialogue in unrelated segments.
        semantic_issues = [issue for issue in semantic_issues
                           if issue.get("segment_id") in target_ids
                           or issue.get("previous_segment_id") in target_ids]
    blocking.extend(
        item for item in semantic_issues if item.get("severity") == "blocking"
    )
    warnings = [
        item for item in semantic_issues if item.get("severity") != "blocking"
    ]
    if duration_issue is not None:
        warnings.append(duration_issue)
    shot_plan = [item.model_dump() for item in output.shots]
    return {
        "schema_version": DIRECTOR_SCHEMA_VERSION,
        "input_fingerprint": payload.get("input_fingerprint"),
        "source_script_revision": execution.get("source_script_revision"),
        "production_revision": execution.get("production_revision"),
        "planning_mode": mode,
        "parent_plan_id": execution.get("parent_plan_id"),
        "target_segment_ids": target_ids,
        "planner_model_id": execution.get("planner_model_id"),
        "video_model_id": execution.get("video_model_id"),
        "video_model_capability_snapshot": capability,
        "skill_bundle": execution.get("skill_bundle") or [],
        "shot_plan": shot_plan,
        "segments": segments,
        "timeline_audit": {
            "target_duration": target_duration,
            "shot_duration": planned_duration,
            "timeline_duration": sum(item["timeline_duration"] for item in segments),
            "generation_duration": sum(item["generation_duration"] for item in segments),
            "trim_duration": sum(item["trim_out"] for item in segments),
        },
        "continuity_report": {
            "status": "blocked" if blocking else (
                "warning" if output.continuity_issues or warnings else "passed"
            ),
            "issues": [
                {"code": "model_continuity_warning", "message": message}
                for message in output.continuity_issues
            ] + warnings + blocking,
        },
        "proposal_status": "pending",
    }


def repair_prompt(payload: dict[str, Any], result: dict[str, Any], message: str, details: dict[str, Any] | None = None) -> str:
    original = str(payload.get("prompt") or "")
    invalid = str(result.get("text") or "")[:20000]
    return (
        f"{original}\n\n上一次输出未通过服务端校验：{message}。"
        "这是唯一一次修复机会。请只返回完整、合法的 JSON 对象。\n"
        f"具体校验错误：{json.dumps(details or {}, ensure_ascii=False, default=str)}\n"
        "continuity_issues 是提醒文本数组，例如 [\"前后服饰需要核对\"]，没有提醒则返回 []。\n"
        f"上一次输出：{invalid}"
    )


def finalize_result(job: Job, result: dict[str, Any], *, repair_attempted: bool) -> dict[str, Any]:
    proposal = validate_model_result(job.payload or {}, result)
    proposal["repair_attempted"] = repair_attempted
    return {
        "proposal": proposal,
        "action_preview": {
            "kind": "episode_director_plan",
            "target_type": TARGET_EPISODE_DIRECTOR,
            "status": "pending",
            "proposed": proposal,
            "source_revision": proposal["source_script_revision"],
        },
        "usage": result.get("usage") or {},
    }
