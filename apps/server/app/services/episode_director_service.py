"""E3 no-chat episode director.

The executor creates an auditable text-planning Job and a review proposal.  It
never creates image/video/audio jobs.  Only an explicit apply call writes a new
EpisodeProductionPlan version.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    JOB_STATUS_SUCCEEDED,
    AgentSkill,
    AgentSkillVersion,
    Asset,
    AssetUsage,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Project,
    Provider,
    ProviderModel,
    Scene,
    Shot,
)
from app.providers.protocols import validate_model_protocol
from app.services import business_executor_service, job_service, segment_plan_service
from app.services import episode_director_pipeline_service as director_pipeline
from app.services.asset_binding_service import binding_role, resolve_asset_binding
from app.services.episode_director_validation import finalize_result as finalize_result
from app.services.episode_director_validation import repair_prompt as repair_prompt
from app.services.episode_director_validation import validate_model_result as validate_model_result
from app.services.episode_preparation_gate import require_preparation
from app.services.skill_runtime import DIRECTOR_CONTRACT
from app.services.video_model_contract import normalize as normalize_video_model
from app.services.video_model_contract import validate_parameters

TARGET_EPISODE_DIRECTOR = "episode_director_plan"
DIRECTOR_SCHEMA_VERSION = "episode_director_plan.v1"


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _model_pair(
    session: AsyncSession, model_id: int, model_type: str
) -> tuple[ProviderModel, Provider]:
    row = (
        await session.execute(
            select(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(ProviderModel.id == model_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("模型不存在")
    model, provider = row
    validate_model_protocol(provider, model)
    if not model.enabled or not provider.enabled:
        raise ConflictError("所选模型或渠道已停用，请重新选择")
    if model.model_type != model_type:
        raise ConflictError(f"所选模型不是{model_type}模型")
    return model, provider


async def get_video_capabilities(
    session: AsyncSession, video_model_id: int
) -> dict[str, Any]:
    model, _provider = await _model_pair(session, video_model_id, "video")
    return normalize_video_model(model)


async def _skill_bundle(
    session: AsyncSession, executor_execution: dict[str, Any]
) -> list[dict[str, Any]]:
    bundle: list[dict[str, Any]] = []
    for selected in executor_execution["skills"]:
        skill = await session.get(AgentSkill, selected["skill_id"])
        if skill is None or not skill.enabled:
            raise ConflictError(f"导演 Skill 已停用或不存在：{selected['name']}")
        version = await session.scalar(
            select(AgentSkillVersion).where(
                AgentSkillVersion.skill_id == skill.id,
                AgentSkillVersion.version == selected["selected_version"],
            )
        )
        if version is None:
            raise ConflictError(
                f"导演 Skill 版本缺失：{skill.name} v{selected['selected_version']}"
            )
        bundle.append(
            {
                "skill_id": skill.id,
                "key": skill.key,
                "version": selected["selected_version"],
                "snapshot": version.snapshot,
            }
        )
    return bundle


async def _production(session: AsyncSession, episode: Episode) -> EpisodeProduction:
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is None:
        production = EpisodeProduction(episode_id=episode.id, settings={})
        session.add(production)
        await session.flush()
    return production


async def _input_snapshot(session: AsyncSession, episode: Episode, *, allow_empty=False) -> dict[str, Any]:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    project = await session.get(Project, episode.project_id)
    if project is None:
        raise NotFoundError("项目不存在")
    rows = list(
        (
            await session.execute(
                select(Shot, Scene)
                .join(Scene, Scene.id == Shot.scene_id)
                .where(
                    Scene.episode_id == episode.id,
                    Shot.owner_id == episode.owner_id,
                    Shot.status != SHOT_STATUS_SUPERSEDED,
                )
                .order_by(Scene.order, Scene.id, Shot.order, Shot.id)
            )
        ).all()
    )
    if not rows and not allow_empty:
        raise ConflictError("本集还没有摄影分镜，请先完成场景与分镜拆解")
    shots = [
        {
            "shot_id": shot.id,
            "scene_id": scene.id,
            "scene_order": scene.order,
            "scene_name": scene.name,
            "scene_location": scene.location,
            "scene_time_of_day": scene.time_of_day,
            "scene_description": scene.description,
            "order": shot.order,
            "duration": float(shot.duration or 0),
            "shot_size": shot.shot_size or "",
            "camera_angle": shot.camera_angle or "",
            "camera_movement": shot.camera_movement or "",
            "action": shot.action or "",
            "dialogue": shot.dialogue or "",
            "audio_note": shot.audio_note or "",
            "is_locked": shot.is_locked,
            "refs": shot.refs or {},
            "prompt": shot.prompt,
            "negative_prompt": shot.negative_prompt,
        }
        for shot, scene in rows
    ]
    usages = list(
        (
            await session.scalars(
                select(AssetUsage)
                .where(
                    AssetUsage.project_id == episode.project_id,
                    AssetUsage.episode_id == episode.id,
                    AssetUsage.shot_id.in_([shot.id for shot, _scene in rows])
                    | AssetUsage.shot_id.is_(None),
                )
                .order_by(AssetUsage.shot_id, AssetUsage.id)
            )
        ).all()
    )
    bindings: list[dict[str, Any]] = []
    for usage in usages:
        asset = await session.get(Asset, usage.asset_id)
        raw = {
            "usage_id": usage.id,
            "shot_id": usage.shot_id,
            "scene_id": usage.scene_id,
            "asset_id": usage.asset_id,
            "asset_name": asset.name if asset else None,
            "asset_type": asset.asset_type if asset else None,
            "usage_type": usage.usage_type,
            "asset_version_id": usage.asset_version_id,
        }
        try:
            canonical = await resolve_asset_binding(
                session,
                project,
                asset_id=usage.asset_id,
                asset_version_id=usage.asset_version_id,
                role=binding_role(raw),
            )
        except (ConflictError, NotFoundError, ValidationError):
            canonical = {}
        bindings.append(
            {
                **raw,
                **canonical,
                "resolved": bool(canonical),
            }
        )
    return {
        "episode_id": episode.id,
        "episode_number": episode.number,
        "title": episode.title,
        "script": episode.script,
        "source_script_revision": episode.script_revision,
        "target_duration": float(episode.duration_estimate or 0),
        "shots": shots,
        "asset_bindings": bindings,
    }


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


def _current_plan_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plan["id"],
        "version": plan["version"],
        "source_script_revision": plan["source_script_revision"],
        "provider_model_id": plan["provider_model_id"],
        "parameters": plan.get("parameters") or {},
        "segments": [
            {"id": item["id"], "order": item["order"], **_segment_input(item)}
            for item in plan["segments"]
        ],
    }


def _prompt(
    input_snapshot: dict[str, Any], capability: dict[str, Any], bundle: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> str:
    schema = {
        "shots": [{
            "shot_id": 1, "duration": 2.5, "shot_size": "中景", "camera_angle": "平视",
            "camera_movement": "缓慢推进", "action": "动作", "subject": "角色名",
            "expression": "克制", "dialogue": "角色：台词", "dialogue_speaker": "角色",
            "dialogue_tone": "平静", "audio_note": "环境声：室内底噪",
        }],
        "segments": [{
            "title": "片段 01", "shot_ids": [1], "generation_duration": 5,
            "prompt": "可直接提交给目标视频模型的完整提示词", "negative_prompt": "",
            "entry_state": "角色站在门外", "exit_state": "角色进入室内", "parameters": {},
        }],
        "continuity_issues": [],
    }
    skill_rules = [
        {"key": item["key"], "version": item["version"], "instruction": item["snapshot"].get("instruction", "")}
        for item in bundle
    ]
    mode = input_snapshot.get("planning_mode") or "replan_episode"
    mode_rule = {
        "replan_episode": "重新规划整集，可重新组合连续且同场景的分镜。",
        "optimize_segment": "只优化 target_segment_ids 中的片段；每个输出片段必须保持原 shot_ids。",
    }.get(mode, "重新规划整集。")
    coverage_rule = (
        "必须覆盖 planning_shots 中的每个 shot_id 且各一次；不要输出非目标分镜。"
        if input_snapshot.get("shots") else
        "输入尚无分镜，请从 source_lines 正文规划完整镜头，按自动草稿约定返回局部编号与来源映射。"
    )
    return (
        "你是无对话的分集导演执行器。只返回一个 JSON 对象，不要 Markdown、解释或代码围栏。\n"
        f"{coverage_rule}"
        "片段只能包含输入顺序中连续且同场景的分镜。generation_duration 必须取 capabilities.durations。"
        "不得编造资产 ID；资产绑定由服务端完成。不要调用或声称已经调用图片、视频或声音生成。\n"
        "必须原样保留来源台词和声音说明；为每条台词填写说话人和语气，为每个分镜填写主体、表情、动作和摄影信息。"
        "每个片段填写可核对的 entry_state 与 exit_state；最终供应商 Prompt 由服务端按结构统一编译。\n"
        "continuity_issues 只放提醒文本字符串，例如 [\"前后服饰需要核对\"]；没有提醒则为 []。"
        "不要在提醒中返回资产对象，实际资产匹配和阻断由服务端核对。\n"
        f"本次模式：{mode}。{mode_rule}\n"
        f"固定输出示例：{json.dumps(schema, ensure_ascii=False)}\n"
        f"Director Skills：{json.dumps(skill_rules, ensure_ascii=False)}\n"
        f"本次合并执行约定：{DIRECTOR_CONTRACT}\n"
        f"视频模型能力：{json.dumps(capability, ensure_ascii=False)}\n"
        f"规划参数：{json.dumps(parameters, ensure_ascii=False)}\n"
        f"分集输入：{json.dumps(input_snapshot, ensure_ascii=False)}"
    )


async def create_plan_job(
    session: AsyncSession,
    episode: Episode,
    *,
    planner_model_id: int,
    video_model_id: int,
    request_id: str,
    mode: str,
    selected_segment_ids: list[int],
    parameters: dict[str, Any],
    auto_prepare: bool = False,
) -> Job:
    if mode not in {"replan_episode", "optimize_segment"}:
        raise ValidationError("不支持的规划模式")
    await require_preparation(session, episode)
    request_spec = dict(planner_model_id=planner_model_id, video_model_id=video_model_id,
                        mode=mode, selected_segment_ids=selected_segment_ids, parameters=parameters)
    await session.execute(update(Episode).where(Episode.id == episode.id).values(number=Episode.number))
    active = (await session.scalars(select(Job).where(
        Job.project_id == episode.project_id, Job.target_id == episode.id,
        Job.target_type.in_([TARGET_EPISODE_DIRECTOR, director_pipeline.TARGET_PIPELINE]),
        Job.status.not_in(["succeeded", "failed", "cancelled"]), Job.deleted_at.is_(None),
    ))).all()
    if any((item.payload or {}).get("request_id") != request_id for item in active):
        raise ConflictError("本集已有片段规划任务，请等待或在任务中心恢复，不要重复提交")
    if auto_prepare:
        if mode != "replan_episode":
            raise ValidationError("正文自动规划仅支持整集草稿，局部调整使用现有提案入口")
        await session.execute(update(Episode).where(Episode.id == episode.id).values(number=Episode.number))
        previous = list((await session.scalars(select(Job).where(
            Job.project_id == episode.project_id, Job.target_id == episode.id,
            Job.target_type.in_([TARGET_EPISODE_DIRECTOR, director_pipeline.TARGET_PIPELINE]),
            Job.deleted_at.is_(None),
        ).order_by(Job.id.desc()))).all())
        for item in previous:
            if item.payload.get("request_id") == request_id:
                if item.payload.get("request_spec") != request_spec:
                    raise ConflictError("请求编号已用于其他规划参数")
                return item
    if episode.finalized_script_revision != episode.script_revision or not episode.duration_estimate:
        raise ConflictError("本集剧本尚未定稿或缺少目标时长")
    executor_execution = await business_executor_service.resolve_execution(
        session, "episode_director", requested_model_id=planner_model_id
    )
    effective_planner_id = int(executor_execution["model"]["id"])
    planner, planner_provider = await _model_pair(session, effective_planner_id, "text")
    video_model, _video_provider = await _model_pair(session, video_model_id, "video")
    capability = normalize_video_model(video_model)
    validate_parameters(capability, parameters)
    production = await _production(session, episode)
    bundle = await _skill_bundle(session, executor_execution)
    snapshot = await _input_snapshot(session, episode, allow_empty=auto_prepare)
    if auto_prepare:
        from app.services.episode_auto_planning_input import enrich_input
        from app.services.shot_lifecycle import require_idle_shots

        await require_idle_shots(session, [shot["shot_id"] for shot in snapshot["shots"]])
        snapshot = await enrich_input(session, episode, production, snapshot)
    current_plan: dict[str, Any] | None = None
    if production.active_plan_id is not None:
        current_plan = await segment_plan_service.get_active_plan(session, episode)
        if (
            current_plan["source_script_revision"] != episode.script_revision
            and mode != "replan_episode"
        ):
            raise ConflictError("当前片段计划对应旧剧本版本，请先整集重规划")
    if mode != "replan_episode" and current_plan is None:
        raise ConflictError("局部AI需要先建立片段计划")

    current_segments = current_plan["segments"] if current_plan else []
    by_id = {item["id"]: item for item in current_segments}
    if mode == "optimize_segment":
        if len(selected_segment_ids) != 1:
            raise ValidationError("单片段优化必须指定且只能指定一个片段")
        if any(segment_id not in by_id for segment_id in selected_segment_ids):
            raise ConflictError("选中的片段不属于当前活动计划")
        selected = set(selected_segment_ids)
        target_segments = [item for item in current_segments if item["id"] in selected]
    else:
        target_segments = current_segments
    target_shot_ids = [
        shot["shot_id"] for segment in target_segments for shot in segment.get("shots") or []
    ]
    snapshot.update({
        "planning_mode": mode,
        "current_plan": _current_plan_snapshot(current_plan) if current_plan else None,
        "target_segment_ids": [item["id"] for item in target_segments],
        "target_shot_ids": target_shot_ids,
        "planning_shots": [
            item for item in snapshot["shots"]
            if mode == "replan_episode" or item["shot_id"] in set(target_shot_ids)
        ],
    })
    audit = {
        "schema_version": DIRECTOR_SCHEMA_VERSION,
        "source_script_revision": episode.script_revision,
        "production_revision": production.revision,
        "planning_mode": mode,
        "parent_plan_id": current_plan["id"] if current_plan else None,
        "planner_model_id": planner.id,
        "video_model_id": video_model.id,
        "video_model_capability_snapshot": capability,
        "skill_bundle": bundle,
        "input": snapshot,
        "parameters": parameters,
        "business_executor": executor_execution,
    }
    digest = _fingerprint(audit)
    await session.execute(update(Episode).where(Episode.id == episode.id).values(number=Episode.number))
    existing_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.owner_id == episode.owner_id,
                    Job.project_id == episode.project_id,
                    Job.target_type.in_([TARGET_EPISODE_DIRECTOR, director_pipeline.TARGET_PIPELINE]),
                    Job.target_id == episode.id,
                )
            )
        ).all()
    )
    for existing in existing_jobs:
        if (existing.payload or {}).get("request_id") != request_id:
            continue
        if (existing.payload or {}).get("input_fingerprint") != digest:
            raise ConflictError("请求编号已用于其他片段规划内容，请重新提交")
        return existing
    if auto_prepare:
        return await director_pipeline.create_pipeline(
            session,
            episode,
            planner=planner,
            planner_provider=planner_provider,
            audit=audit,
            request_spec=request_spec,
            request_id=request_id,
            input_fingerprint=digest,
        )
    prompt = _prompt(snapshot, capability, bundle, parameters)
    job = await job_service.create_text_job(
        session,
        episode.owner_id,
        provider_model_id=planner.id,
        prompt=prompt,
        project_id=episode.project_id,
        parameters={},
    )
    job.target_type = TARGET_EPISODE_DIRECTOR
    job.target_id = episode.id
    job.max_attempts = 1
    job.payload = {
        **job.payload,
        "auto_prepare": auto_prepare,
        "request_spec": request_spec,
        "request_id": request_id,
        "input_fingerprint": digest,
        "video_model_id": video_model.id,
        "director_execution": audit,
    }
    await session.flush()
    return job


async def apply_proposal(
    session: AsyncSession,
    episode: Episode,
    job_id: int,
    *,
    expected_production_revision: int,
) -> dict[str, Any]:
    job = await session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.owner_id == episode.owner_id,
            Job.project_id == episode.project_id,
            Job.target_type == TARGET_EPISODE_DIRECTOR,
            Job.target_id == episode.id,
        )
    )
    if job is None:
        raise NotFoundError("片段规划记录不存在")
    if job.status != JOB_STATUS_SUCCEEDED or not isinstance(job.result, dict):
        raise ConflictError("片段规划尚未成功完成")
    proposal = job.result.get("proposal")
    if not isinstance(proposal, dict):
        raise ConflictError("片段规划没有可确认的结构化提案")
    if proposal.get("proposal_status") == "confirmed" and proposal.get("confirmed_plan_id"):
        plan = await session.get(EpisodeProductionPlan, proposal["confirmed_plan_id"])
        if plan is None:
            raise ConflictError("已确认计划不存在")
        return await segment_plan_service.serialize_plan(session, plan)
    if proposal.get("source_script_revision") != episode.script_revision:
        raise ConflictError("剧本版本已变化，请重新生成片段规划")
    await require_preparation(session, episode)
    mode = proposal.get("planning_mode") or "replan_episode"
    proposal_issues = (proposal.get("continuity_report") or {}).get("issues") or []
    semantic_blocking_codes = {"dialogue_speaker_missing", "state_discontinuity"}
    if (proposal.get("continuity_report") or {}).get("status") == "blocked" and (
        mode == "replan_episode"
        or any(item.get("code") in semantic_blocking_codes for item in proposal_issues)
    ):
        raise ConflictError("提案仍有阻断问题，不能确认制作计划")
    video_model, _provider = await _model_pair(session, int(proposal["video_model_id"]), "video")
    current_capability = normalize_video_model(video_model)
    if _fingerprint(current_capability) != _fingerprint(proposal["video_model_capability_snapshot"]):
        raise ConflictError("视频模型能力配置已变化，请重新规划")
    production = await _production(session, episode)
    parent_plan_id = proposal.get("parent_plan_id")
    if parent_plan_id is not None and production.active_plan_id != parent_plan_id:
        raise ConflictError("活动片段计划已变化，请重新执行局部AI")

    shots = list(
        (
            await session.scalars(
                select(Shot)
                .join(Scene, Scene.id == Shot.scene_id)
                .where(Scene.episode_id == episode.id)
            )
        ).all()
    )
    by_id = {item.id: item for item in shots}
    for item in proposal["shot_plan"]:
        shot = by_id.get(int(item["shot_id"]))
        if shot is None:
            raise ConflictError("提案引用的分镜已删除，请重新规划")
        shot.duration = float(item["duration"])
        shot.shot_size = item.get("shot_size") or None
        shot.camera_angle = item.get("camera_angle") or None
        shot.camera_movement = item.get("camera_movement") or None
        shot.action = item.get("action") or None
        shot.dialogue = item.get("dialogue") or None
        shot.audio_note = item.get("audio_note") or None
    blocked = (proposal.get("continuity_report") or {}).get("status") == "blocked"
    source_type = (
        "ai"
        if mode == "replan_episode" and parent_plan_id is None
        else "replan"
        if mode == "replan_episode"
        else mode
    )
    plan = await segment_plan_service.create_plan(
        session,
        episode,
        expected_production_revision=expected_production_revision,
        provider_model_id=video_model.id,
        model_capability_snapshot=current_capability,
        parameters={
            "source": "episode_director",
            "director_job_id": job.id,
            "input_fingerprint": proposal.get("input_fingerprint"),
            "skill_bundle": proposal.get("skill_bundle") or [],
            "shot_plan": proposal.get("shot_plan") or [],
            "planning_mode": mode,
            "target_segment_ids": proposal.get("target_segment_ids") or [],
        },
        status="draft" if blocked else "confirmed",
        segments=proposal["segments"],
        source_type=source_type,
        parent_plan_id=parent_plan_id,
    )
    confirmed = {**proposal, "proposal_status": "confirmed", "confirmed_plan_id": plan["id"]}
    job.result = {
        **job.result,
        "proposal": confirmed,
        "action_preview": {**(job.result.get("action_preview") or {}), "status": "applied", "proposed": confirmed},
    }
    await session.flush()
    return plan


async def reject_proposal(session: AsyncSession, episode: Episode, job_id: int) -> Job:
    job = await session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.owner_id == episode.owner_id,
            Job.project_id == episode.project_id,
            Job.target_type == TARGET_EPISODE_DIRECTOR,
            Job.target_id == episode.id,
        )
    )
    if job is None:
        raise NotFoundError("片段规划记录不存在")
    if job.status != JOB_STATUS_SUCCEEDED or not isinstance(job.result, dict):
        raise ConflictError("只有已完成的片段规划可以驳回")
    proposal = job.result.get("proposal") or {}
    if proposal.get("proposal_status") == "confirmed":
        raise ConflictError("已确认计划不能通过驳回删除；请创建新版本")
    rejected = {**proposal, "proposal_status": "rejected"}
    job.result = {
        **job.result,
        "proposal": rejected,
        "action_preview": {**(job.result.get("action_preview") or {}), "status": "rejected", "proposed": rejected},
    }
    await session.flush()
    return job
