"""Recoverable small-plan and per-segment episode director pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import ValidationError as SchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, ValidationError
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    Episode,
    Job,
    Provider,
    ProviderModel,
    utcnow,
)
from app.schemas.episode_director import DirectorModelOutput
from app.schemas.episode_director_pipeline import DirectorOutlineOutput
from app.services import job_service, text_model_policy_service
from app.services.episode_director_prompt_context import frozen_skill_rules, segment_handoff_context
from app.services.episode_director_skill_projection import PROJECTION_VERSION
from app.services.episode_duration_policy import episode_duration_guidance
from app.services.video_model_contract import validate_segment

TARGET_PIPELINE = "episode_director_pipeline"
TARGET_OUTLINE = "episode_director_outline"
TARGET_SEGMENT = "episode_director_segment"
PIPELINE_VERSION = 4


def _json_object(result: dict[str, Any], label: str) -> dict[str, Any]:
    text = str(result.get("text") or "").strip()
    if not text:
        raise ValidationError(f"{label}没有返回内容")
    if text.startswith("```"):
        first_break = text.find("\n")
        last_fence = text.rfind("```")
        if first_break >= 0 and last_fence > first_break:
            text = text[first_break + 1:last_fence].strip()
    candidates = [text]
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first and (first or last != len(text) - 1):
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    likely_truncated = first >= 0 and (last < first or not text.endswith("}"))
    raise ValidationError(
        f"{label}未返回完整 JSON；已保存原始响应，可在任务中心核对",
        details={
            "failure_kind": "truncated_json" if likely_truncated else "invalid_json",
            "output_chars": len(text),
            "first_non_whitespace": text[:1],
            "last_non_whitespace": text[-1:],
            "top_level_closed": bool(text.endswith("}")),
        },
    )


def _outline_prompt(execution: dict[str, Any], *, compact_rules: bool = False) -> str:
    snapshot = execution["input"]
    capability = execution["video_model_capability_snapshot"]
    existing = snapshot.get("shots") or []
    source = {
        "target_duration": snapshot.get("target_duration"),
        "source_lines": snapshot.get("source_lines") or [],
        "existing_shots": existing,
        "existing_scenes": snapshot.get("scene_snapshot") or [],
        "assets": [
            {
                "asset_id": item.get("asset_id"),
                "name": item.get("asset_name"),
                "type": item.get("asset_type"),
                "aliases": item.get("aliases") or [],
                "description": item.get("description"),
            }
            for item in snapshot.get("asset_catalog") or []
        ],
    }
    schema = {
        "scenes": [{"scene_id": 1, "name": "场景", "location": "地点", "time_of_day": "日", "description": "说明"}],
        "shots": [{"shot_id": 1, "scene_id": 1, "beat_id": "beat-001", "duration": 3, "source_lines": [1], "asset_ids": [1], "unresolved_names": []}],
        "segments": [{"key": "segment-001", "title": "片段 01", "shot_ids": [1], "generation_duration": 5}],
    }
    existing_rule = (
        "已有分镜：scenes 为 []；锁定镜头必须原样保留。非锁定镜头可重拆，新增镜头用大于现有最大 shot_id 的局部编号，"
        "scene_id 使用已有场景 ID；重拆时覆盖每个正文行，台词/声音只归属一个镜头。"
        if existing else
        "没有分镜：按正文建立局部 scene_id/shot_id；每个非空正文行必须被 source_lines 覆盖，动作行可支撑多个镜头，但每句台词/声音只归属一个镜头；场景按剧情连续分组。"
    )
    duration_guidance = (episode_duration_guidance(snapshot["target_duration"])
                         if snapshot.get("target_duration") else "本集目标时长未设置；按剧情自然估时。")
    return (
        "你是分集导演的轻量规划器。只规划场景、镜头时长、正文来源、资产引用和视频片段边界，"
        "不要写镜头摄影细节、完整提示词或解释。只返回一个 JSON 对象。\n"
        f"{existing_rule} {duration_guidance} segments 必须按 shots 顺序完整覆盖且不重复，"
        "一个片段不能跨场景；generation_duration 必须来自视频模型 durations，分镜数不能超过上限。"
        "资产只能使用 assets 中的 asset_id；不能唯一判断时写入 unresolved_names，不能猜测。"
        "asset_ids 是剧情所需资产的完整关联，不是实际发送的视频参考图列表；"
        "同一戏剧行动/反应可共用 beat_id 作为规划提示；系统会按正文来源行集合生成最终稳定锚点；"
        "先按来源对白长度估算说话时间，再给动作、停顿和反应留时间；"
        "按完整行动与反应选择边界，不在一句台词中间强行切开，不以空镜凑目标时长。"
        "Director Skills 仅用于本阶段的规划判断，不能扩大来源范围、改写正文或改变固定输出结构。"
        "不要为了参考图上限删掉角色、服装、场景或道具，图片输入由生成阶段单独校验。\n"
        f"固定结构：{json.dumps(schema, ensure_ascii=False)}\n"
        f"Director Skills：{json.dumps(frozen_skill_rules(execution, stage='outline' if compact_rules else None), ensure_ascii=False)}\n"
        f"视频模型能力：{json.dumps(capability, ensure_ascii=False)}\n"
        f"规划输入：{json.dumps(source, ensure_ascii=False)}"
    )


def _validate_outline(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    try:
        output = DirectorOutlineOutput.model_validate(
            _json_object(result, "片段边界规划")
        )
    except SchemaError as exc:
        raise ValidationError(
            "片段边界规划结构不完整",
            details={"errors": exc.errors(include_input=False)[:10]},
        ) from exc
    execution = payload["director_execution"]
    snapshot = execution["input"]
    capability = execution["video_model_capability_snapshot"]
    existing = snapshot.get("shots") or []
    shots = output.shots
    shot_ids = [item.shot_id for item in shots]
    if len(shot_ids) != len(set(shot_ids)):
        raise ValidationError("轻量规划包含重复镜头编号")
    source_lines = {int(item["line"]) for item in snapshot.get("source_lines") or []}
    catalog_ids = {int(item["asset_id"]) for item in snapshot.get("asset_catalog") or []}
    for item in shots:
        if len(item.source_lines) != len(set(item.source_lines)) or not set(item.source_lines) <= source_lines:
            raise ValidationError("轻量规划引用了不存在或重复的正文行")
        if len(item.asset_ids) != len(set(item.asset_ids)) or not set(item.asset_ids) <= catalog_ids:
            raise ValidationError("轻量规划引用了不存在或重复的资产")
    if existing:
        if output.scenes:
            raise ValidationError("已有分镜规划不能新增场景")
        source_by_id = {int(item["shot_id"]): item for item in existing}
        maximum_existing_id = max(source_by_id)
        scene_ids = {int(item["id"]) for item in snapshot.get("scene_snapshot") or []}
        for item in shots:
            source = source_by_id.get(item.shot_id)
            if source is None:
                if item.shot_id <= maximum_existing_id or item.scene_id not in scene_ids:
                    raise ValidationError("新增镜头必须使用新局部编号及已有场景")
            elif item.scene_id != int(source["scene_id"]):
                raise ValidationError("已有镜头不能改变场景归属")
            elif source.get("is_locked") and abs(item.duration - float(source["duration"])) > 0.05:
                raise ValidationError("锁定镜头时长不能修改")
        locked_ids = {int(item["shot_id"]) for item in existing if item.get("is_locked")}
        if not locked_ids <= set(shot_ids):
            raise ValidationError("锁定镜头不能从规划中移除")
        original_ids = [int(item["shot_id"]) for item in existing]
        if any(shot_ids.index(shot_id) != original_ids.index(shot_id) for shot_id in locked_ids):
            raise ValidationError("锁定镜头的位置不能改变")
        if shot_ids != original_ids:
            covered_lines = {line for item in shots for line in item.source_lines}
            if covered_lines != source_lines or any(not item.source_lines for item in shots):
                raise ValidationError("重拆镜头必须完整标注每个非空正文行的来源")
    else:
        scenes = {item.scene_id: item for item in output.scenes}
        if len(scenes) != len(output.scenes) or not scenes:
            raise ValidationError("首次规划必须返回不重复的场景")
        if {item.scene_id for item in shots} != set(scenes):
            raise ValidationError("镜头场景必须完整且不能包含空场景")
        covered_lines = {line for item in shots for line in item.source_lines}
        if covered_lines != source_lines or any(not item.source_lines for item in shots):
            raise ValidationError("轻量规划必须完整覆盖正文每个非空行")
        grouped = [
            item.scene_id for index, item in enumerate(shots)
            if index == 0 or item.scene_id != shots[index - 1].scene_id
        ]
        if grouped != list(scenes):
            raise ValidationError("场景与镜头必须按剧情顺序连续排列")
    submitted = [shot_id for item in output.segments for shot_id in item.shot_ids]
    if submitted != shot_ids or len(submitted) != len(set(submitted)):
        raise ValidationError("片段边界必须按顺序且各一次覆盖全部镜头")
    positions = {shot_id: index for index, shot_id in enumerate(shot_ids)}
    by_id = {item.shot_id: item for item in shots}
    keys = [item.key for item in output.segments]
    if len(keys) != len(set(keys)):
        raise ValidationError("片段稳定标识不能重复")
    for segment in output.segments:
        indices = [positions[value] for value in segment.shot_ids]
        if indices != list(range(min(indices), max(indices) + 1)):
            raise ValidationError("片段只能使用连续镜头")
        if len({by_id[value].scene_id for value in segment.shot_ids}) != 1:
            raise ValidationError("一个片段不能跨场景")
        validate_segment(
            capability,
            generation_duration=segment.generation_duration,
            shot_count=len(segment.shot_ids),
            timeline_duration=sum(by_id[value].duration for value in segment.shot_ids),
        )
    return output.model_dump()


def _shot_snapshot(outline: dict[str, Any], execution: dict[str, Any]) -> list[dict[str, Any]]:
    frozen = execution["input"]
    existing = {int(item["shot_id"]): deepcopy(item) for item in frozen.get("shots") or []}
    scenes = {
        int(item["scene_id"]): item for item in outline.get("scenes") or []
    } or {
        int(item["id"]): item for item in frozen.get("scene_snapshot") or []
    }
    result = []
    for item in outline["shots"]:
        shot_id = int(item["shot_id"])
        if shot_id in existing:
            result.append({**existing[shot_id], "duration": float(item["duration"]), "order": len(result) + 1})
            continue
        scene = scenes[int(item["scene_id"])]
        result.append({
            "shot_id": shot_id,
            "scene_id": int(item["scene_id"]),
            "scene_order": int(scene.get("order") or list(scenes).index(int(item["scene_id"])) + 1),
            "scene_name": scene["name"],
            "scene_location": scene.get("location") or "",
            "scene_time_of_day": scene.get("time_of_day") or "",
            "scene_description": scene.get("description") or "",
            "order": len(result) + 1,
            "duration": float(item["duration"]),
            "shot_size": "",
            "camera_angle": "",
            "camera_movement": "",
            "action": "",
            "dialogue": "",
            "audio_note": "",
            "is_locked": False,
            "refs": {},
            "prompt": None,
            "negative_prompt": None,
        })
    return result


def _segment_execution(parent: Job, outline: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    execution = deepcopy(parent.payload["director_execution"])
    frozen = execution["input"]
    all_shots = _shot_snapshot(outline, execution)
    target_ids = [int(value) for value in segment["shot_ids"]]
    shots = [item for item in all_shots if int(item["shot_id"]) in set(target_ids)]
    source_by_id = {int(item["shot_id"]): item for item in outline["shots"]}
    catalog = {int(item["asset_id"]): item for item in frozen.get("asset_catalog") or []}
    bindings = []
    for shot in shots:
        source = source_by_id[int(shot["shot_id"])]
        for asset_id in source.get("asset_ids") or []:
            asset = catalog[int(asset_id)]
            bindings.append({
                **asset,
                "shot_id": int(shot["shot_id"]),
                "scene_id": int(shot["scene_id"]),
                "resolved": bool(asset.get("resolved")),
            })
    execution["planning_mode"] = "replan_episode"
    execution["input"] = {
        **frozen,
        "shots": shots,
        "planning_shots": shots,
        "asset_bindings": bindings,
        "target_duration": sum(float(item["duration"]) for item in shots),
        "target_segment_ids": [],
        "target_shot_ids": target_ids,
        "current_plan": None,
    }
    return execution


def _segment_prompt(parent: Job, outline: dict[str, Any], segment: dict[str, Any]) -> str:
    execution = _segment_execution(parent, outline, segment)
    snapshot = execution["input"]
    source_ids = {
        line for shot in outline["shots"] if int(shot["shot_id"]) in set(segment["shot_ids"])
        for line in shot.get("source_lines") or []
    }
    source_lines = [row for row in snapshot.get("source_lines") or [] if row["line"] in source_ids]
    assets = [
        {"asset_id": row.get("asset_id"), "name": row.get("asset_name"), "type": row.get("asset_type"),
         "description": row.get("description"), "prompt_anchor": row.get("prompt_anchor")}
        for row in snapshot.get("asset_catalog") or []
        if any(int(row.get("asset_id") or 0) in set(shot.get("asset_ids") or []) for shot in outline["shots"] if int(shot["shot_id"]) in set(segment["shot_ids"]))
    ]
    schema = {
        "shots": [{"shot_id": segment["shot_ids"][0], "duration": 3, "shot_size": "中景", "camera_angle": "平视", "camera_movement": "推进", "action": "动作", "subject": "主体", "expression": "表情", "dialogue": "", "dialogue_speaker": "", "dialogue_tone": "", "audio_note": ""}],
        "segments": [{"title": segment["title"], "shot_ids": segment["shot_ids"], "generation_duration": segment["generation_duration"], "prompt": "完整片段脚本", "negative_prompt": "", "entry_state": "进入状态", "exit_state": "结束状态", "parameters": {}}],
        "continuity_issues": [],
    }
    return (
        "你是分集导演的单片段编剧。只完成当前一个片段，不得输出其他片段，只返回 JSON。"
        "shots 必须按给定 shot_ids 逐个返回，duration 必须保持规划值；segments 必须且只能有一项，"
        "shot_ids、generation_duration 必须原样保持。完整保留来源台词以及 BGM、配乐、环境声、音效原句，"
        "严格使用固定结构中的字段，片段镜头编号只用 shot_ids，不添加 shot_id 或 shot_id_list。"
        "continuity_issues 只返回提醒文本数组，没有提醒则返回 []，不返回 description 等诊断对象。"
        "同一台词或声音说明只能归属一个引用了该正文行的镜头，不得因拆镜头重复朗读。"
        "已有镜头非空的 dialogue/audio_note 由系统原样继承，勿向这些字段追加解释或执行说明。"
        "锁定镜头的时长、摄影、动作、台词及声音均由系统保留，不要改写。"
        "填写景别、机位、运镜、主体、表情、动作、说话人、语气和明确的进入/结束状态。"
        "只读剧情衔接来自冻结正文或旧分镜的节选，不是相邻片段已生成的结果，也不是完整状态。"
        "仅据明确来源核对人物位置、道具、动作和声音的交接；不得复制相邻片段台词或输出其镜头，"
        "不得让角色提前知道后文信息。same_scene 为 false 时保留合法转场，不强行沿用上一场环境。"
        "在规划时长内给对白、动作、停顿和反应留时间；若无法自然演完，"
        "在 continuity_issues 中给出简短审阅提示，不得删词、加速念词、改时长或编造过渡剧情。"
        "Director Skills 只在当前片段范围内应用，固定输出结构与来源保护规则优先。"
        "不要编造资产 ID，不要声称已经生成媒体。\n"
        f"固定结构：{json.dumps(schema, ensure_ascii=False)}\n"
        f"Director Skills：{json.dumps(frozen_skill_rules(execution, stage='segment' if parent.payload.get('pipeline_version', 0) >= 4 else None), ensure_ascii=False)}\n"
        f"当前片段：{json.dumps(segment, ensure_ascii=False)}\n"
        f"镜头约束：{json.dumps(snapshot['shots'], ensure_ascii=False)}\n"
        f"正文来源：{json.dumps(source_lines, ensure_ascii=False)}\n"
        f"只读剧情衔接：{json.dumps(segment_handoff_context(parent.payload['director_execution'], outline, segment), ensure_ascii=False)}\n"
        f"可用资产：{json.dumps(assets, ensure_ascii=False)}\n"
        f"视频模型能力：{json.dumps(execution['video_model_capability_snapshot'], ensure_ascii=False)}"
    )


async def create_pipeline(
    session: AsyncSession,
    episode: Episode,
    *,
    planner: ProviderModel,
    planner_provider: Provider,
    audit: dict[str, Any],
    request_spec: dict[str, Any],
    request_id: str,
    input_fingerprint: str,
) -> Job:
    audit = deepcopy(audit)
    audit["skill_rule_projection"] = {
        "version": PROJECTION_VERSION,
        "stages": {
            stage: frozen_skill_rules(audit, stage=stage) for stage in ("outline", "segment")
        },
    }
    parent = Job(
        owner_id=episode.owner_id,
        project_id=episode.project_id,
        provider_id=planner.provider_id,
        job_type="text_batch",
        target_type=TARGET_PIPELINE,
        target_id=episode.id,
        status=JOB_STATUS_PROCESSING,
        progress=0,
        max_attempts=1,
        provider=planner_provider.name,
        model=planner.model_id,
        started_at=utcnow(),
        payload={
            "pipeline_version": PIPELINE_VERSION,
            "expected_total": 1,
            "expected_segments": 0,
            "request_spec": request_spec,
            "request_id": request_id,
            "input_fingerprint": input_fingerprint,
            "provider_model_id": planner.id,
            "video_model_id": audit["video_model_id"],
            "auto_prepare": True,
            "director_execution": audit,
        },
        result={"stage": "planning_boundaries", "total": 1, "completed": 0, "segments": []},
    )
    session.add(parent)
    await session.flush()
    child = await job_service.create_text_job(
        session,
        episode.owner_id,
        provider_model_id=planner.id,
        prompt=_outline_prompt(audit, compact_rules=True),
        project_id=episode.project_id,
        parameters={},
    )
    child.parent_job_id = parent.id
    child.target_type = TARGET_OUTLINE
    child.target_id = episode.id
    child.max_attempts = 1
    child.payload = {
        **child.payload,
        "director_execution": audit,
        "director_pipeline": {"stage": "outline", "parent_job_id": parent.id},
    }
    parent.execution_policy_snapshot = deepcopy(child.execution_policy_snapshot)
    parent.payload = {**parent.payload, "outline_job_id": child.id}
    await session.flush()
    return parent


async def finalize_outline(
    session: AsyncSession, job: Job, result: dict[str, Any]
) -> dict[str, Any]:
    outline = _validate_outline(job.payload or {}, result)
    parent = await session.get(Job, job.parent_job_id) if job.parent_job_id else None
    if parent is None or parent.target_type != TARGET_PIPELINE:
        raise ConflictError("导演父流程不存在")
    if parent.status == JOB_STATUS_CANCELLED or parent.deleted_at is not None:
        raise ConflictError("导演流程已取消或删除，不能恢复旧结果")
    from app.services.segment_plan_core_service import _production

    episode = await session.get(Episode, parent.target_id)
    execution = parent.payload["director_execution"]
    if episode is None or episode.script_revision != execution["source_script_revision"]:
        raise ConflictError("正文已变化，已保留模型结果，请核对后重新规划")
    production = await _production(session, episode)
    if (production.revision != execution["production_revision"]
            or production.active_plan_id != execution.get("parent_plan_id")):
        raise ConflictError("制作计划已变化，不能恢复旧规划并继续生成片段")
    existing = list((await session.scalars(select(Job).where(
        Job.parent_job_id == parent.id, Job.target_type == TARGET_SEGMENT,
    ))).all())
    if not existing:
        for order, segment in enumerate(outline["segments"], 1):
            child = await job_service.create_text_job(
                session,
                parent.owner_id,
                provider_model_id=int(parent.payload["provider_model_id"]),
                prompt=_segment_prompt(parent, outline, segment),
                project_id=parent.project_id,
                parameters={},
            )
            child.parent_job_id = parent.id
            child.target_type = TARGET_SEGMENT
            child.target_id = parent.target_id
            child.max_attempts = 1
            child.payload = {
                **child.payload,
                "director_execution": _segment_execution(parent, outline, segment),
                "director_pipeline": {
                    "stage": "segment",
                    "parent_job_id": parent.id,
                    "segment_key": segment["key"],
                    "segment_order": order,
                    "segment_outline": segment,
                    "outline": outline,
                },
            }
            text_model_policy_service.inherit_job_snapshot(parent, child)
        parent.payload = {
            **parent.payload,
            "expected_total": 1 + len(outline["segments"]),
            "expected_segments": len(outline["segments"]),
        }
        parent.result = {
            **(parent.result or {}),
            "stage": "writing_segments",
            "total": 1 + len(outline["segments"]),
            "outline": outline,
        }
    return {
        "pipeline_stage": "outline",
        "outline": outline,
        "usage": result.get("usage") or {},
    }


async def finalize_segment(job: Job, result: dict[str, Any]) -> dict[str, Any]:
    raw = _json_object(result, "片段脚本")
    try:
        output = DirectorModelOutput.model_validate(raw)
    except SchemaError as exc:
        raise ValidationError(
            "片段脚本结构不完整",
            details={"errors": exc.errors(include_input=False)[:10]},
        ) from exc
    pipeline = dict((job.payload or {}).get("director_pipeline") or {})
    outline = pipeline.get("segment_outline") or {}
    expected_ids = [int(value) for value in outline.get("shot_ids") or []]
    if len(output.segments) != 1:
        raise ValidationError("单片段任务必须且只能返回一个片段")
    segment = output.segments[0]
    if segment.shot_ids != expected_ids:
        raise ValidationError("单片段任务改变了镜头范围或顺序")
    if abs(segment.generation_duration - float(outline["generation_duration"])) > 0.05:
        raise ValidationError("单片段任务改变了模型生成时长")
    # These are approved source fields, not creative output. Reattach them from
    # the frozen input instead of asking an LLM to copy them byte for byte.
    sources = {
        int(item["shot_id"]): item
        for item in job.payload["director_execution"]["input"].get("shots") or []
    }
    for shot in output.shots:
        source = sources.get(shot.shot_id) or {}
        protected_fields = (
            ("duration", "shot_size", "camera_angle", "camera_movement", "action", "dialogue", "audio_note")
            if source.get("is_locked") else ("dialogue", "audio_note")
        )
        for field in protected_fields:
            if source.get("is_locked") or str(source.get(field) or "").strip():
                setattr(shot, field, source[field])
    from app.services.episode_director_validation import validate_model_result

    proposal = validate_model_result(job.payload or {}, {**result, "text": output.model_dump_json()})
    return {
        "pipeline_stage": "segment",
        "segment_key": pipeline.get("segment_key"),
        "segment_order": pipeline.get("segment_order"),
        "raw": output.model_dump(),
        "proposal": proposal,
        "usage": result.get("usage") or {},
    }


def _usable_children(children: list[Job]) -> list[Job]:
    return [
        item for item in children
        if (item.resolution or {}).get("status") not in {"superseded", "replacement_pending"}
    ]


def _recovery_summary(failed: list[Job]) -> dict[str, Any]:
    pending = exhausted = channel_check = ordinary = 0
    for item in failed:
        submission = dict((item.payload or {}).get("text_submission") or {})
        recovery = dict((item.payload or {}).get("response_recovery") or {})
        if (item.failure_detail or {}).get("action") == "retry":
            ordinary += 1
        elif submission.get("response_received"):
            if recovery.get("last_reprocess_error_code"):
                exhausted += 1
            else:
                pending += 1
        elif submission.get("status") == "submitted":
            channel_check += 1
        else:
            ordinary += 1
    return {
        "local_reprocess_pending": pending,
        "local_reprocess_exhausted": exhausted,
        "channel_check_required": channel_check,
        "ordinary_retry_required": ordinary,
        "paid_recall_allowed": (
            bool(failed) and pending == 0 and ordinary == 0
            and exhausted + channel_check == len(failed)
        ),
    }


async def aggregate_parent(session: AsyncSession, parent: Job) -> Job:
    children = _usable_children(list((await session.scalars(
        select(Job).where(Job.parent_job_id == parent.id).order_by(Job.id)
    )).all()))
    outline_jobs = [item for item in children if item.target_type == TARGET_OUTLINE]
    segment_jobs = [item for item in children if item.target_type == TARGET_SEGMENT]
    outline_job = next((item for item in reversed(outline_jobs) if item.status == JOB_STATUS_SUCCEEDED), None)
    expected_segments = int((parent.payload or {}).get("expected_segments") or 0)
    succeeded_segments = [item for item in segment_jobs if item.status == JOB_STATUS_SUCCEEDED]
    failed = [item for item in children if item.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}]
    recovery_summary = _recovery_summary(failed)
    completed = sum(item.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED} for item in children)
    total = int((parent.payload or {}).get("expected_total") or max(1, len(children)))
    parent.progress = min(99, round(completed * 100 / total)) if total else 0
    parent.result = {
        **(parent.result or {}),
        "stage": "writing_segments" if outline_job else "planning_boundaries",
        "total": total,
        "completed": completed,
        "succeeded_segments": len(succeeded_segments),
        "expected_segments": expected_segments,
        "children": [
            {
                "job_id": item.id,
                "stage": (item.payload or {}).get("director_pipeline", {}).get("stage"),
                "segment_key": (item.payload or {}).get("director_pipeline", {}).get("segment_key"),
                "segment_order": (item.payload or {}).get("director_pipeline", {}).get("segment_order"),
                "status": item.status,
                "error_code": item.error_code,
                "error_message": item.error_message,
                "output_diagnostic": ((item.payload or {}).get("attempt_history") or [{}])[-1].get("output_diagnostic"),
            }
            for item in children
        ],
    }
    parent.payload = {**(parent.payload or {}), "recovery_summary": recovery_summary}
    active = [item for item in children if item.status not in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}]
    if failed and not active:
        parent.status = JOB_STATUS_FAILED
        parent.error_code = "DIRECTOR_PARTIAL_FAILURE" if succeeded_segments else "DIRECTOR_PIPELINE_FAILED"
        parent.error_message = (
            (f"片段脚本完成 {len(succeeded_segments)}/{expected_segments}；" if expected_segments else "片段边界处理失败；")
            + f"{failed[0].error_message or '存在失败范围，请在任务中心处理'}"
        )
        parent.finished_at = utcnow()
        return parent
    if outline_job and expected_segments and len(succeeded_segments) == expected_segments:
        outline = (outline_job.result or {}).get("outline") or {}
        ordered = sorted(succeeded_segments, key=lambda item: int((item.result or {}).get("segment_order") or 0))
        raw = {
            "scenes": outline.get("scenes") or [],
            "shot_sources": [
                {key: value for key, value in item.items() if key != "duration"}
                for item in outline.get("shots") or []
            ],
            "shots": [shot for item in ordered for shot in (item.result or {}).get("raw", {}).get("shots", [])],
            "segments": [segment for item in ordered for segment in (item.result or {}).get("raw", {}).get("segments", [])],
            "continuity_issues": [issue for item in ordered for issue in (item.result or {}).get("raw", {}).get("continuity_issues", [])],
        }
        usage: dict[str, int] = {}
        for item in children:
            for key, value in dict((item.result or {}).get("usage") or {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
        try:
            from app.services.episode_auto_planning_service import save_draft

            async with session.begin_nested():
                saved = await save_draft(
                    session,
                    parent,
                    {"text": json.dumps(raw, ensure_ascii=False), "usage": usage},
                )
        except AppError as exc:
            from app.services.job_state_result_service import safe_output_diagnostic
            parent.status = JOB_STATUS_FAILED
            previous_recovery = dict((parent.payload or {}).get("response_recovery") or {})
            parent.error_code = "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
            parent.error_message = f"导演子任务结果已全部保存，但本地写入失败：{exc.message}"
            parent.payload = {
                **(parent.payload or {}),
                "output_diagnostic": safe_output_diagnostic(exc.details),
                "response_recovery": {
                    **previous_recovery,
                    "kind": "director_child_results",
                    "output_diagnostic": safe_output_diagnostic(exc.details),
                    "status": "available",
                    "model_called": False,
                    "last_reprocess_error_code": (
                        exc.code if previous_recovery else None
                    ),
                    "last_reprocess_error_message": (
                        exc.message[:500] if previous_recovery else None
                    ),
                },
            }
            parent.finished_at = utcnow()
            return parent
        parent.result = {
            **saved,
            "pipeline": parent.result,
            "usage": usage,
        }
        parent.status = JOB_STATUS_SUCCEEDED
        parent.progress = 100
        parent.error_code = None
        parent.error_message = None
        parent.finished_at = utcnow()
        return parent
    parent.status = JOB_STATUS_PROCESSING
    parent.error_code = None
    parent.error_message = None
    parent.finished_at = None
    return parent


async def confirm_recall(
    session: AsyncSession,
    job: Job,
    *,
    actor_id: int,
    channel_checked: bool,
    reason: str,
) -> Job:
    """Explicitly resubmit only failed pipeline children after local recovery."""
    parent = job
    if job.target_type in {TARGET_OUTLINE, TARGET_SEGMENT} and job.parent_job_id:
        parent = await session.get(Job, job.parent_job_id)
    if parent is None or parent.target_type != TARGET_PIPELINE:
        raise ConflictError("导演流水线父任务不存在")
    from app.services.team_access import same_team
    if not await same_team(session, parent.owner_id, actor_id):
        raise ConflictError("无权恢复该导演任务")
    episode = await session.get(Episode, parent.target_id)
    frozen = dict((parent.payload or {}).get("director_execution") or {}).get("input") or {}
    if episode is None or not await same_team(session, episode.owner_id, actor_id):
        raise ConflictError("分集不存在或无权访问")
    if episode.script_revision != int(frozen.get("source_script_revision") or 0):
        raise ConflictError("分集正文版本已变化，请重新生成片段规划")
    failed = list((await session.scalars(select(Job).where(
        Job.parent_job_id == parent.id,
        Job.status.in_({JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}),
    ))).all())
    failed = _usable_children(failed)
    if not failed:
        raise ConflictError("没有需要恢复的导演失败范围")
    for child in failed:
        submission = dict((child.payload or {}).get("text_submission") or {})
        recovery = dict((child.payload or {}).get("response_recovery") or {})
        if submission.get("response_received"):
            if not recovery.get("last_reprocess_error_code"):
                raise ConflictError(f"子任务 #{child.id} 请先执行无费用的本地重新处理")
        elif submission.get("status") == "submitted":
            if not channel_checked:
                raise ConflictError(f"子任务 #{child.id} 请先核对渠道后台并确认未生成")
        else:
            raise ConflictError(f"子任务 #{child.id} 可普通重试，无需付费确认恢复")
    recalled_at = utcnow().isoformat()
    for child in failed:
        child.status = JOB_STATUS_QUEUED
        child.progress = 0
        child.attempts = 0
        child.available_at = None
        child.error_code = None
        child.error_message = None
        child.result = None
        child.started_at = None
        child.finished_at = None
        child.worker_id = None
        child.lease_expires_at = None
        child.payload = {
            **(child.payload or {}),
            "recovery": {
                "kind": "confirmed_paid_recall",
                "confirmed_by": actor_id,
                "confirmed_at": recalled_at,
                "reason": reason,
            },
        }
    history = list((parent.payload or {}).get("recall_history") or [])
    history.append({
        "child_job_ids": [child.id for child in failed],
        "confirmed_by": actor_id,
        "confirmed_at": recalled_at,
        "reason": reason,
    })
    parent.payload = {
        **(parent.payload or {}),
        "recall_history": history[-20:],
        "recovery_summary": {},
    }
    parent.status = JOB_STATUS_PROCESSING
    parent.finished_at = None
    parent.error_code = None
    parent.error_message = None
    await session.flush()
    return parent
