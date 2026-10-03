"""M3 剧本研读与资产拆解业务逻辑：分批研读、资产候选生成与确认入库。

本模块处理长剧本的分批 AI 研读和结构化资产拆解的完整流程。
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_TYPE_STORY_BIBLE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    CreationArtifact,
    CreationSession,
    Job,
    Provider,
)
from app.providers.protocols import effective_protocol
from app.services import (
    asset_service,
    job_service,
    project_service,
    script_finalization_service,
)
from app.services.creation_agent_service import (
    _attach_agent_execution,
    _resolve_agent_execution,
)
from app.services.creation_breakdown_planning import (
    AUDIO_REQUIREMENT_TYPES,
    MAX_EPISODES_PER_WORK_UNIT,
    REQUIREMENT_RESULT_KEYS,
    BreakdownWorkUnit,
    breakdown_scope_key,
    build_breakdown_work_units,
    structured_output_parameters,
    restore_work_unit,
    visual_asset_key_context,
)
from app.services.creation_breakdown_sources import (
    _artifact_source,
    _validate_breakdown_story_source,
)
from app.services.creation_production_context import build_production_context
from app.services.creation_breakdown_prompt_context import batch_prompt_context
from app.services.creation_session_service import (
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
    JOB_TARGET_SCRIPT_STUDY_BATCH,
    JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP,
    SCRIPT_STUDY_BATCH_SIZE,
    append_message,
)
from app.services.script_source_snapshot import build_script_snapshot

R4_REQUIREMENT_TYPES = tuple(REQUIREMENT_RESULT_KEYS)
BREAKDOWN_ACTIVE_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
}


async def _create_asset_breakdown_child(
    session: AsyncSession,
    item: CreationSession,
    parent: Job,
    *,
    model_id: int,
    execution: dict[str, Any],
    instruction_prefix: str,
    unit: BreakdownWorkUnit,
    batch_index: int,
    total_batches: int,
    context: dict[str, Any],
    protocol: str,
    capabilities: list[str],
    source_revisions: dict[str, int],
    input_fingerprint: str,
    story_source: dict[str, int],
    requested_numbers: list[int],
    scope_key: str,
) -> Job:
    episode_payload = unit.episode_payload()
    prompt = f"{instruction_prefix}\n\n" + _asset_breakdown_prompt(
        {**context, "episodes": episode_payload},
        list(unit.requirement_types),
        group=unit.group,
    )
    child = await job_service.create_text_job(
        session,
        item.owner_id,
        provider_model_id=model_id,
        prompt=prompt,
        project_id=item.project_id,
        parameters={
            **structured_output_parameters(
                protocol=protocol,
                capabilities=capabilities,
            ),
            "agent_workspace": "script_asset_breakdown_batch",
            "batch_index": batch_index,
            "total_batches": total_batches,
            "episode_numbers": unit.episode_numbers,
            "source_script_revisions": source_revisions,
            "input_fingerprint": input_fingerprint,
            "story_source": story_source,
            "production_context": context.get("production_context") or {},
            "requirement_types": list(unit.requirement_types),
            "requirement_group": unit.group,
            "input_chars": unit.input_chars,
            "output_budget_mode": "model",
            "source_ranges": unit.source_ranges,
            "requested_episode_numbers": requested_numbers,
            "scope_key": scope_key,
        },
    )
    child.target_type = JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH
    child.target_id = item.id
    child.parent_job_id = parent.id
    child.max_attempts = 1
    _attach_agent_execution(child, execution)
    return child


async def create_batched_script_study_job(
    session: AsyncSession, item: CreationSession, *, finalized_only: bool = False
) -> Job:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    episodes = (
        await script_finalization_service.require_project_scripts_finalized(
            session, item.project_id, item.owner_id
        )
        if finalized_only
        else await project_service.list_episodes(session, item.project_id)
    )
    if not episodes:
        raise ConflictError("未识别到可研读的分集")
    active = await session.scalar(
        select(Job.id).where(
            Job.owner_id == item.owner_id,
            Job.target_type.in_(
                {JOB_TARGET_SCRIPT_STUDY_BATCH, JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP}
            ),
            Job.target_id == item.id,
            Job.status.in_(
                [JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING]
            ),
        )
    )
    if active is not None:
        raise ConflictError("剧本正在分批研读")
    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "script_study"
    )

    snapshot = build_script_snapshot(episodes)
    extraction = {**snapshot, "optional_extraction": "episode_outline"} if finalized_only else {}
    batches = [
        episodes[offset : offset + SCRIPT_STUDY_BATCH_SIZE]
        for offset in range(0, len(episodes), SCRIPT_STUDY_BATCH_SIZE)
    ]
    parent = Job(
        owner_id=item.owner_id,
        project_id=item.project_id,
        job_type="text",
        status=JOB_STATUS_PROCESSING,
        progress=0,
        target_type=JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP,
        target_id=item.id,
        provider_id=model.provider_id,
        model=model.model_id,
        payload={
            "batch_size": SCRIPT_STUDY_BATCH_SIZE,
            "total_batches": len(batches),
            "agent_execution": execution,
            **extraction,
        },
        result={"total": len(batches), "completed": 0, "child_job_ids": []},
    )
    session.add(parent)
    await session.flush()

    children: list[Job] = []
    for batch_index, batch_episodes in enumerate(batches):
        inputs = [
            {
                "number": episode.number,
                "title": episode.title,
                "script": episode.script or "",
            }
            for episode in batch_episodes
        ]
        expected_numbers = [episode.number for episode in batch_episodes]
        prompt = f"""{instruction_prefix}

你是短剧总编剧。逐集研读下列剧本，优化每集标题、摘要、戏剧目标和结尾悬念；保留核心剧情、人物关系和集号。
严格输出单个 JSON 对象，不要 Markdown、代码围栏或解释文字：
{{"reply":"本批研读说明","episodes":[{{"number":1,"title":"","synopsis":"","dramatic_goal":"","cliffhanger":""}}]}}
episodes 必须按输入顺序返回，集号必须严格等于 {expected_numbers}，不得增删、合并或拆分分集。
本批剧本：{json.dumps(inputs, ensure_ascii=False)}"""
        child = await job_service.create_text_job(
            session,
            item.owner_id,
            provider_model_id=model.id,
            prompt=prompt,
            project_id=item.project_id,
            parameters={
                "agent_workspace": "script_study_batch",
                "batch_index": batch_index,
                "total_batches": len(batches),
                "episode_numbers": expected_numbers,
                **extraction,
            },
        )
        child.target_type = JOB_TARGET_SCRIPT_STUDY_BATCH
        child.target_id = item.id
        child.parent_job_id = parent.id
        _attach_agent_execution(child, execution)
        children.append(child)
    parent.result = {
        "total": len(children),
        "completed": 0,
        "child_job_ids": [child.id for child in children],
    }
    settings = dict(item.settings)
    settings["script_study"] = {
        "status": "running",
        "batch_size": SCRIPT_STUDY_BATCH_SIZE,
        "total_batches": len(batches),
        "completed_batches": 0,
        "detected_episode_count": len(episodes),
        "parent_job_id": parent.id,
        "batches": {},
        **snapshot,
    }
    if finalized_only:
        states = dict(settings.get("optional_extractions") or {})
        states["episode_outline"] = {"status": "running", "job_id": parent.id, **snapshot}
        settings["optional_extractions"] = states
    item.settings = settings
    await append_message(
        session,
        item.id,
        "user",
        "script_study_request",
        f"已保留原始剧本和本地识别的 {len(episodes)} 集，开始分 {len(batches)} 批执行 AI 研读。",
        job_id=parent.id,
    )
    await session.flush()
    return parent


async def create_uploaded_script_optimization_job(
    session: AsyncSession, item: CreationSession
) -> Job:
    """兼容旧调用；新流程统一使用可续跑的分批研读。"""
    return await create_batched_script_study_job(session, item)


async def create_script_asset_breakdown_job(
    session: AsyncSession,
    item: CreationSession,
    *,
    requirement_types: list[str] | None = None,
    episode_numbers: list[int] | None = None,
) -> Job:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    episodes = await script_finalization_service.require_project_scripts_finalized(
        session, item.project_id, item.owner_id
    )
    snapshot = build_script_snapshot(episodes)
    source_revisions = snapshot["source_script_revisions"]
    input_fingerprint = snapshot["input_fingerprint"]
    selected_types = list(dict.fromkeys(requirement_types or R4_REQUIREMENT_TYPES))
    previous_breakdown = dict((item.settings or {}).get("asset_breakdown") or {})
    if (requirement_types or episode_numbers) and previous_breakdown.get("candidates"):
        previous_fingerprint = previous_breakdown.get("input_fingerprint")
        if previous_fingerprint and previous_fingerprint != input_fingerprint:
            raise ConflictError("正式剧本已变化，不能将旧候选混入局部重提，请按当前剧本重新提取全部资产")
    invalid_types = sorted(set(selected_types) - set(R4_REQUIREMENT_TYPES))
    if invalid_types:
        raise ConflictError(f"不支持的制作需求类型：{invalid_types}")
    requested_numbers = sorted(set(episode_numbers or []))
    existing_numbers = {episode.number for episode in episodes}
    invalid_numbers = sorted(set(requested_numbers) - existing_numbers)
    if invalid_numbers:
        raise ConflictError(f"拆解范围包含不存在的分集：{invalid_numbers}")
    selected_episodes = [
        episode
        for episode in episodes
        if not requested_numbers or episode.number in requested_numbers
    ]
    scripted = [episode for episode in selected_episodes if (episode.script or "").strip()]
    if len(scripted) != len(selected_episodes):
        missing = [
            episode.number for episode in selected_episodes if not (episode.script or "").strip()
        ]
        raise ConflictError(f"请先完成全部分集正文，缺少第 {missing} 集")
    if not scripted:
        raise ConflictError("剧本正文为空，无法拆解资产")
    bible = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
            CreationArtifact.status == ARTIFACT_STATUS_CONFIRMED,
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    production_context = await build_production_context(session, item, episodes, bible)
    story_source = dict(production_context.get("story_source") or _artifact_source(bible))
    await _validate_breakdown_story_source(session, item, story_source)
    existing_assets = await asset_service.list_assets(session, item.project_id)
    existing_asset_context = [
        {
            "id": asset["id"],
            "asset_type": asset["asset_type"],
            "name": asset["name"],
            "asset_key": (asset.get("attributes") or {}).get("asset_key"),
            "aliases": list((asset.get("attributes") or {}).get("aliases") or []),
            "requirement_type": (asset.get("attributes") or {}).get("requirement_type"),
            "audio_purpose": (asset.get("attributes") or {}).get("audio_purpose"),
            "linked_character_asset_id": (asset.get("attributes") or {}).get(
                "linked_character_asset_id"
            ),
            "has_final_version": any(
                version.get("is_final") for version in asset.get("versions", [])
            ),
        }
        for asset in existing_assets
    ]
    # Pending candidates are the naming reference until the first library commit.
    candidate_reference = [
        {"name": row.get("name"), "aliases": row.get("aliases") or [],
         "asset_key": (row.get("attributes") or {}).get("asset_key"),
         "requirement_type": row.get("requirement_type", row.get("asset_type")),
         "description": str(row.get("description") or "")[:300]}
        for row in previous_breakdown.get("candidates") or []
        if row.get("selected", True) and not row.get("merged_into_candidate_id")
        and row.get("requirement_type", row.get("asset_type")) in selected_types
        and (not requested_numbers or set(row.get("episode_numbers") or []) & set(requested_numbers)
             or row.get("requirement_type") in {"character", "character_voice"})
    ] if requirement_types or episode_numbers else []
    active = await session.scalar(
        select(Job.id).where(
            Job.owner_id == item.owner_id,
            Job.target_type.in_(
                {
                    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
                    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
                    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
                }
            ),
            Job.target_id == item.id,
            Job.status.in_(BREAKDOWN_ACTIVE_STATUSES),
        )
    )
    if active is not None:
        raise ConflictError("剧本资产正在拆解")
    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "outline", "asset_breakdown"
    )
    provider = await session.get(Provider, model.provider_id)
    if provider is None:
        raise ConflictError("资产拆解模型渠道不存在")
    protocol = effective_protocol(provider, model)
    visual_units, audio_units = build_breakdown_work_units(scripted, selected_types)
    def scope_key(unit: BreakdownWorkUnit) -> str:
        return breakdown_scope_key(
            input_fingerprint=input_fingerprint,
            group=unit.group,
            requirement_types=unit.requirement_types,
            episode_numbers=unit.episode_numbers,
            provider_model_id=model.id,
            skill_key=execution.get("skill_key"),
            skill_version=execution.get("skill_version"),
            source_ranges=unit.source_ranges,
        )

    visual_scope_keys = [scope_key(unit) for unit in visual_units]
    audio_scope_keys = [scope_key(unit) for unit in audio_units]
    total_units = len(visual_units) + len(audio_units)
    initial_units = visual_units or audio_units
    stage_audio = bool(visual_units and audio_units)
    parent = Job(
        owner_id=item.owner_id,
        project_id=item.project_id,
        job_type="text",
        status=JOB_STATUS_PROCESSING,
        progress=0,
        target_type=JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
        target_id=item.id,
        provider_id=model.provider_id,
        model=model.model_id,
        payload={
            "batch_size": MAX_EPISODES_PER_WORK_UNIT,
            "batch_strategy": "adaptive_episodes_and_lossless_script_chunks",
            "total_batches": total_units,
            "expected_total": total_units,
            "visual_batches": len(visual_units),
            "audio_batches": len(audio_units),
            "audio_queued": not stage_audio,
            "pending_audio_units": [
                {"episode_numbers": unit.episode_numbers, "scope_key": key,
                 "source_ranges": unit.source_ranges}
                for unit, key in zip(audio_units, audio_scope_keys, strict=True)
            ] if stage_audio else [],
            "scope_order": [*visual_scope_keys, *audio_scope_keys],
            "visual_scope_keys": visual_scope_keys,
            "agent_execution": execution,
            "instruction_prefix": instruction_prefix,
            "provider_model_id": model.id,
            "protocol": protocol,
            "model_capabilities": list(model.capabilities or []),
            "source_script_revisions": source_revisions,
            "input_fingerprint": input_fingerprint,
            "story_source": story_source,
            "requirement_types": selected_types,
            "requested_episode_numbers": requested_numbers,
            "existing_assets": existing_asset_context,
            "candidate_reference": candidate_reference,
        },
        result={"total": total_units, "completed": 0, "child_job_ids": []},
    )
    session.add(parent)
    await session.flush()
    children: list[Job] = []
    base_context = {
        "production_context": production_context,
        "candidate_reference": candidate_reference,
        "story_bible": bible.content if bible is not None else None,
        "existing_assets": existing_asset_context,
    }
    initial_scope_keys = visual_scope_keys or audio_scope_keys
    for batch_index, (unit, unit_scope_key) in enumerate(
        zip(initial_units, initial_scope_keys, strict=True)
    ):
        child = await _create_asset_breakdown_child(
            session,
            item,
            parent,
            model_id=model.id,
            execution=execution,
            instruction_prefix=instruction_prefix,
            unit=unit,
            batch_index=batch_index,
            total_batches=total_units,
            context=base_context,
            protocol=protocol,
            capabilities=list(model.capabilities or []),
            source_revisions={
                str(episode.id): episode.script_revision for episode in unit.episodes
            },
            input_fingerprint=input_fingerprint,
            story_source=story_source,
            requested_numbers=requested_numbers,
            scope_key=unit_scope_key,
        )
        children.append(child)
    parent.result = {
        "total": total_units,
        "completed": 0,
        "child_job_ids": [child.id for child in children],
    }
    settings = dict(item.settings)
    previous_breakdown = dict(settings.get("asset_breakdown") or {})
    scoped = bool(requirement_types or episode_numbers)
    settings["asset_breakdown"] = {
        "status": "running",
        "completed": False,
        "batch_size": MAX_EPISODES_PER_WORK_UNIT,
        "batch_strategy": "adaptive_1_2_episodes_by_script_chars",
        "total_batches": total_units,
        "visual_batches": len(visual_units),
        "audio_batches": len(audio_units),
        "audio_queued": not stage_audio,
        "stage": "visual" if stage_audio else (initial_units[0].group if initial_units else "complete"),
        "completed_batches": 0,
        "parent_job_id": parent.id,
        "batches": {},
        "scope_order": [*visual_scope_keys, *audio_scope_keys],
        "visual_scope_keys": visual_scope_keys,
        "source_script_revisions": source_revisions,
        "input_fingerprint": input_fingerprint,
        "story_source": story_source,
        "production_context": production_context,
        "requirement_types": selected_types,
        "requested_episode_numbers": requested_numbers,
        "scope_mode": "partial" if scoped else "full",
        "preserved_candidates": list(previous_breakdown.get("candidates") or []) if scoped else [],
    }
    item.settings = settings
    await append_message(
        session,
        item.id,
        "user",
        "asset_breakdown_request",
        f"开始按 1—2 集和资产类别拆分为 {total_units} 个任务，处理 {len(scripted)} 集剧本资产。",
        job_id=parent.id,
    )
    await session.flush()
    return parent


async def queue_pending_audio_breakdown_jobs(
    session: AsyncSession,
    item: CreationSession,
    parent: Job,
    visual_breakdowns: list[dict[str, Any]],
) -> list[Job]:
    payload = dict(parent.payload or {})
    pending = list(payload.get("pending_audio_units") or [])
    if payload.get("audio_queued") or not pending:
        return []
    episodes = await project_service.list_episodes(session, item.project_id or 0)
    by_number = {episode.number: episode for episode in episodes}
    audio_types = [
        value
        for value in payload.get("requirement_types") or []
        if value in AUDIO_REQUIREMENT_TYPES
    ]
    progress = dict((item.settings or {}).get("asset_breakdown") or {})
    children: list[Job] = []
    visual_count = int(payload.get("visual_batches") or 0)
    total_batches = int(payload.get("total_batches") or 0)
    execution = dict(payload.get("agent_execution") or {})
    for offset, pending_unit in enumerate(pending):
        numbers = (
            list(pending_unit.get("episode_numbers") or [])
            if isinstance(pending_unit, dict)
            else list(pending_unit)
        )
        pending_scope_key = (
            str(pending_unit.get("scope_key") or "")
            if isinstance(pending_unit, dict)
            else ""
        )
        batch_episodes = tuple(by_number[int(number)] for number in numbers if int(number) in by_number)
        ranges = pending_unit.get("source_ranges") if isinstance(pending_unit, dict) else None
        unit = restore_work_unit(batch_episodes, audio_types, "audio", ranges)
        unit_scope_key = pending_scope_key or breakdown_scope_key(
            input_fingerprint=str(payload.get("input_fingerprint") or ""),
            group=unit.group,
            requirement_types=unit.requirement_types,
            episode_numbers=unit.episode_numbers,
            provider_model_id=int(payload["provider_model_id"]),
            skill_key=execution.get("skill_key"),
            skill_version=execution.get("skill_version"),
            source_ranges=unit.source_ranges,
        )
        context = {
            "production_context": dict(progress.get("production_context") or {}),
            "existing_assets": list(payload.get("existing_assets") or []),
            "candidate_reference": list(payload.get("candidate_reference") or []),
            "visual_asset_keys": visual_asset_key_context(
                visual_breakdowns,
                episode_numbers=unit.episode_numbers,
            ),
        }
        child = await _create_asset_breakdown_child(
            session,
            item,
            parent,
            model_id=int(payload["provider_model_id"]),
            execution=execution,
            instruction_prefix=str(payload.get("instruction_prefix") or ""),
            unit=unit,
            batch_index=visual_count + offset,
            total_batches=total_batches,
            context=context,
            protocol=str(payload.get("protocol") or "openai_compatible"),
            capabilities=list(payload.get("model_capabilities") or []),
            source_revisions={
                str(episode.id): episode.script_revision for episode in unit.episodes
            },
            input_fingerprint=str(payload.get("input_fingerprint") or ""),
            story_source=dict(payload.get("story_source") or {}),
            requested_numbers=list(payload.get("requested_episode_numbers") or []),
            scope_key=unit_scope_key,
        )
        children.append(child)
    parent.payload = {
        **payload,
        "audio_queued": True,
        "pending_audio_units": [],
    }
    parent.result = {
        **(parent.result or {}),
        "child_job_ids": [
            *list((parent.result or {}).get("child_job_ids") or []),
            *[child.id for child in children],
        ],
    }
    return children


def _asset_breakdown_prompt(
    context: dict[str, Any], requirement_types: list[str] | None = None, *, group: str | None = None
) -> str:
    context = batch_prompt_context(context)
    selected_types = requirement_types or list(R4_REQUIREMENT_TYPES)
    group_label = "视觉资产" if group == "visual" else "声音资产" if group == "audio" else "制作资产"
    return f"""你是影视制片资产统筹。请从本批剧本中提取需要保持一致性的{group_label}。
严格输出单个 JSON 对象，不要 Markdown、代码围栏或解释文字。结构必须为：
{{"reply":"拆解说明","characters":[],"costumes":[],"scenes":[],"props":[],"character_voices":[],"music":[],"ambience":[],"sound_effects":[],"usage_records":[]}}
本次只提取这些制作需求类型：{json.dumps(selected_types, ensure_ascii=False)}。未选择的数组必须返回空数组。
输出紧凑 JSON，reply 只写简短结论；description、prompt_anchor 和原文摘录应简明准确，避免重复长篇解释。必须完整输出所有资产与使用记录并闭合 JSON，不得为了压缩文字省略资产。
资产基础资料与使用记录分层返回。八组资产每项只包含 name、aliases、episode_numbers、description、prompt_anchor、attributes；attributes.asset_key 必须使用“需求类型:规范化名称”的稳定键。
顶层 usage_records 每项包含 asset_key、episode_number、scene、source_excerpt、performance、timing、needs_review；asset_key 必须引用本次返回的资产。
aliases 是同一资产在不同分集中的别名数组；episode_numbers 和 usage_records.episode_number 只能使用 allowed_episode_numbers 中的集号。
只以 episodes 中的 script 正文作为本批资产出场证据；超长单集会按 source_range 分段，必须覆盖本段全部正文，不得省略段尾。preceding_context、following_context 仅用于理解段落衔接，不要提取其中独有的资产或使用记录。identity_reference 和 existing_assets 仅用于统一身份、名称和基础设定，不代表本批出场。不得复制其他分集或其他片段的出场范围、事件或使用记录。
角色 attributes 必须包含 character_role（仅可为 lead、supporting、extra、unclassified），并可含 role、age、appearance；造型可含 character_name、hair、makeup、injury、stage；场景可含 location、time、weather、lighting、atmosphere；道具可含 owner、material、story_function、story_state；角色声音可含 character_name、pitch、texture、pace、accent、language；配乐、环境声、音效必须分别放入对应数组并在 attributes 标明 audio_purpose。
声音资产任务必须读取 visual_asset_keys，并在 attributes.linked_asset_key 中引用对应角色或场景的稳定键；确实没有对应视觉资产时标记 needs_review=true，不得伪造关联。
人物表情、动作、语气、视线、站位写入 performance，音乐/声音出现与结束位置写入 timing，不要把这些临时使用信息覆盖为角色基础设定。
scene、source_excerpt、performance、timing 不明确时可留空或返回 null；不得编造原文或时间。episode_number 和 asset_key 必须有效，出现集数必须覆盖全部 usage_records。
必须保留中英文人物名、别名、服装、场景、声音和原文定位；production_context 中的 source_records 是来源记录，无法确认的内容在 attributes 中标记 needs_review=true，不得改写原文或把推测写成事实。
优先复用 existing_assets；名称不同但确认为同一对象时保留别名，不要重复创建。造型用 character_name 关联人物；场景变体用 base_scene_name 和 variant_label 关联基础地点。若正文、设定和已有资产事实冲突，在 attributes.source_conflicts 中并列记录来源与差异并标记 needs_review，不能自动裁定或覆盖已有资料。
Story Bible 中的人物只作为叙事背景，不代表已经进入制作资产库。角色候选必须在本批最终剧本正文中实际出现，并且确实需要跨镜头或跨分集保持视觉一致性；不得因为 Story Bible 列出了 core、recurring、phase 或 functional 人物就自动创建角色资产。群众、路人、一次性功能角色若无需一致性，不创建独立角色资产。
同一批次内合并同名资产和别名，只保留剧本中实际出现或明确需要的资产。无对白不创建角色声音需求，原文没有音乐需求不创建配乐；不生成分镜、图片、音频或视频。
剧本上下文：{json.dumps(context, ensure_ascii=False)}"""
