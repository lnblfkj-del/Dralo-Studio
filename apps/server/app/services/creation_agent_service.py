"""M3 创作 Agent 业务逻辑：Agent 执行配置、Job 创建与提案管理。

本模块处理 Agent 的统一执行配置、Job 创建和结构化提案的应用/拒绝。
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    ARTIFACT_STATUS_SUPERSEDED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_STORY_BIBLE,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
    CreationArtifact,
    CreationSession,
    Job,
    ProviderModel,
)
from app.schemas.creation import (
    EpisodeOutlineContent,
    StoryBibleContent,
)
from app.services import job_service, provider_service
from app.services.creation_session_service import (
    append_message,
    create_artifact,
)
from app.services.narrative_prompt_service import (
    outline_agent_strategy_prompt,
    story_bible_strategy_prompt,
)
from app.services.outline_structure_review_service import review_for_settings


async def _resolve_agent_execution(
    session: AsyncSession,
    agent_key: str,
    surface: str,
) -> tuple[ProviderModel, dict[str, Any], str]:
    """Resolve and snapshot the Agent configuration used by a creation task.

    Standard creation flows remain compatible with installations that have no
    fixed Skill yet. Once a Skill is explicitly configured it is validated and
    becomes part of both the prompt and the immutable job audit snapshot.
    """
    model, route_source = await provider_service.resolve_agent_model(
        session, agent_key
    )
    settings = await provider_service.get_ai_settings(session)
    if agent_key == "outline":
        instruction = settings.outline_agent_instruction
    elif agent_key == "script":
        instruction = settings.script_agent_instruction
    else:
        raise ConflictError("当前创作流程没有对应的专业 Agent")
    from app.services.skill_runtime import creation_contract, creation_skills

    configured_skills = await provider_service.get_agent_skills(session, agent_key)
    bound_skills = creation_skills(configured_skills, agent_key, surface)
    skill = next((item for item in bound_skills if item.output_modality == "text"), None)
    execution = {
        "agent": agent_key,
        "surface": surface,
        "route_source": route_source,
        "provider_model_id": model.id,
        "model_id": model.model_id,
        "instruction": instruction,
        "skill_selection_surface": surface,
        "excluded_skill_keys": [item.key for item in configured_skills if item not in bound_skills],
        "skill_id": skill.id if skill is not None else None,
        "skill_key": skill.key if skill is not None else None,
        "skill_name": skill.name if skill is not None else None,
        "skill_version": skill.version if skill is not None else None,
        "skills": [
            {"id": item.id, "key": item.key, "name": item.name, "version": item.version,
             "capability_type": item.capability_type, "allowed_tools": list(item.allowed_tools),
             "write_policy": item.write_policy, "instruction": item.instruction}
            for item in bound_skills
        ],
    }
    prompt_parts = [f"专业 Agent 运行边界：{instruction}"]
    for item in bound_skills:
        prompt_parts.append(f"已绑定 Skill {item.name}（{item.key}，V{item.version}）：{item.instruction}")
    prompt_parts.append(creation_contract(surface))
    return model, execution, "\n".join(prompt_parts)


def _attach_agent_execution(job: Job, execution: dict[str, Any]) -> None:
    job.payload = {**job.payload, "agent_execution": execution}


async def create_creation_job(
    session: AsyncSession,
    item: CreationSession,
    target_type: str,
    prompt: str,
    active_message: str,
    provider_model_id: int | None = None,
    parameters: dict[str, Any] | None = None,
    agent_key: str | None = None,
    execution_surface: str | None = None,
    exclude_job_id: int | None = None,
) -> Job:
    from app.services.creation_session_service import CREATION_ARTIFACT_TYPES
    active = await session.scalar(select(Job.id).where(
        Job.id != exclude_job_id if exclude_job_id is not None else True,
        Job.owner_id == item.owner_id,
        Job.target_type.in_(CREATION_ARTIFACT_TYPES),
        Job.target_id == item.id,
        Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING, "downloading"]),
    ))
    if active is not None:
        raise ConflictError(active_message)
    execution: dict[str, Any] | None = None
    if agent_key is not None:
        model, execution, instruction_prefix = await _resolve_agent_execution(
            session, agent_key, execution_surface or target_type
        )
        if provider_model_id is not None and provider_model_id != model.id:
            raise ConflictError("任务指定模型与 Agent 路由不一致")
        prompt = f"{instruction_prefix}\n\n{prompt}"
    else:
        model = (
            await session.get(ProviderModel, provider_model_id)
            if provider_model_id is not None
            else await provider_service.get_default_text_model(session)
        )
    if model is None:
        raise ConflictError("配置的文本模型不存在")
    job = await job_service.create_text_job(
        session, item.owner_id, provider_model_id=model.id, prompt=prompt,
        project_id=item.project_id, parameters=parameters or {},
    )
    job.target_type = target_type
    job.target_id = item.id
    if execution_surface == "asset_breakdown":
        job.max_attempts = 1
    if execution is not None:
        _attach_agent_execution(job, execution)
    await session.flush()
    return job


async def create_outline_agent_job(
    session: AsyncSession,
    item: CreationSession,
    message: str,
    attachment_chunk_ids: list[int],
    uploaded_attachments: list[dict[str, str]] | None = None,
    character_batch_completion: dict[str, Any] | None = None,
    character_outline_coverage: dict[str, Any] | None = None,
    story_overview_adjustment: dict[str, Any] | None = None,
    event_timeline_adjustment: dict[str, Any] | None = None,
    optimize_all: bool = False,
) -> Job:
    from app.services.creation_session_service import (
        JOB_TARGET_OUTLINE_AGENT,
        chunk_reference_text,
    )
    ai_settings = await provider_service.get_ai_settings(session)
    if not ai_settings.outline_agent_enabled:
        raise ConflictError("大纲 Agent 已由管理员停用")
    uploaded_attachments = uploaded_attachments or []
    from app.services.source_index_service import resolve_source_text
    chunks = chunk_reference_text(await resolve_source_text(session, item))
    selected_chunk_ids = (
        attachment_chunk_ids
        if attachment_chunk_ids
        else [] if uploaded_attachments else list(
            range(min(len(chunks), ai_settings.outline_agent_max_chunks))
        )
    )
    if len(selected_chunk_ids) > ai_settings.outline_agent_max_chunks:
        raise ConflictError(
            f"大纲 Agent 每次最多读取 {ai_settings.outline_agent_max_chunks} 个剧本片段"
        )
    if len(set(selected_chunk_ids)) != len(selected_chunk_ids) or any(
        chunk_id < 0 or chunk_id >= len(chunks) for chunk_id in selected_chunk_ids
    ):
        raise ConflictError("剧本片段不存在，请刷新后重试")
    latest: dict[str, Any] = {}
    source_artifacts: dict[str, dict[str, int | None]] = {}
    for artifact_type in (ARTIFACT_TYPE_STORY_BIBLE, ARTIFACT_TYPE_EPISODE_OUTLINE):
        artifact = await session.scalar(
            select(CreationArtifact)
            .where(
                CreationArtifact.session_id == item.id,
                CreationArtifact.artifact_type == artifact_type,
                CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
            )
            .order_by(CreationArtifact.version.desc())
            .limit(1)
        )
        source_artifacts[artifact_type] = {
            "id": artifact.id if artifact is not None else None,
            "version": artifact.version if artifact is not None else 0,
            "revision": artifact.revision if artifact is not None else 0,
        }
        if artifact is not None:
            if artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
                from app.services.outline_management_service import normalize
                normalized = await normalize(session, item, artifact)
                latest[artifact_type] = {"episodes": normalized["episodes"]}
            else:
                latest[artifact_type] = artifact.content
    batch_completion = None
    if character_batch_completion is not None:
        from app.services.story_planning_service import batch_completion_scope
        batch_completion = batch_completion_scope(
            character_batch_completion,
            source_artifacts[ARTIFACT_TYPE_STORY_BIBLE],
            latest.get(ARTIFACT_TYPE_STORY_BIBLE, {}),
        )
    outline_coverage = None
    if character_outline_coverage is not None:
        from app.services.outline_character_coverage import coverage_scope
        story_snapshot = latest.get(ARTIFACT_TYPE_STORY_BIBLE)
        outline_snapshot = latest.get(ARTIFACT_TYPE_EPISODE_OUTLINE)
        if not story_snapshot or not outline_snapshot:
            raise ConflictError("生成角色覆盖提案前需要故事设定和分集大纲")
        outline_coverage = coverage_scope(
            character_outline_coverage,
            source_artifacts,
            story_snapshot,
            outline_snapshot,
        )
    story_adjustment = None
    if story_overview_adjustment is not None or event_timeline_adjustment is not None:
        from app.services.story_planning_service import story_section_scope
        request = story_overview_adjustment or event_timeline_adjustment
        section = "overview" if story_overview_adjustment is not None else "events"
        story_snapshot = latest.get(ARTIFACT_TYPE_STORY_BIBLE)
        if not story_snapshot:
            raise ConflictError("调整故事资料前需要先建立故事设定")
        story_adjustment = story_section_scope(
            request,
            source_artifacts[ARTIFACT_TYPE_STORY_BIBLE],
            story_snapshot,
            section,
        )
    source_attachments = [
        {**chunks[chunk_id], "source_name": "项目原稿"}
        for chunk_id in selected_chunk_ids
    ]
    uploaded_chunks: list[dict[str, Any]] = []
    uploaded_metadata: list[dict[str, Any]] = []
    for attachment in uploaded_attachments:
        name = attachment["name"].strip()
        content = attachment["content"].strip()
        file_chunks = chunk_reference_text(content)
        uploaded_metadata.append({
            "name": name,
            "char_count": len(content),
            "chunk_count": len(file_chunks),
        })
        uploaded_chunks.extend(
            {**chunk, "source_name": name}
            for chunk in file_chunks
        )
    attachments = [*source_attachments, *uploaded_chunks]
    if len(attachments) > ai_settings.outline_agent_max_chunks:
        raise ConflictError(
            f"本轮附件共拆分为 {len(attachments)} 个片段，最多允许 "
            f"{ai_settings.outline_agent_max_chunks} 个；请缩短附件后重试"
        )
    skill = await provider_service.get_outline_agent_skill(session)
    structure_strategy = (
        story_bible_strategy_prompt(item.settings)
        if story_adjustment and story_adjustment["section"] == "events"
        else outline_agent_strategy_prompt(item.settings)
    )
    prompt = f"""你是短剧大纲 Agent。根据用户要求决定是否修改结构化内容，只输出 JSON，不要 Markdown。
JSON 顶层只能使用 reply, story_bible, episode_outline。reply 必填；无需修改的结构化字段必须填 null。
story_bible 必须包含 title, logline, genre, tone, audience, world, themes, characters, event_timeline；title、logline、genre、tone、audience、world 必须是字符串，themes 必须是字符串数组。
characters 必须保留 name, role, goal, conflict, arc；保留已有character_id、aliases、年龄age、人物介绍description、性格personality、外貌appearance、服装costume、声线voice、importance、narrative_function、appearance_scope及其他原有资料。未知新资料可留空，不伪造精确年龄。改名须保留character_id和原名别名。
event_timeline 必须是对象数组，每项只能使用 title, summary, episode_hint；title、summary 必须是字符串，episode_hint 只能是 1-300 的整数或 null。
episode_outline 顶层为 episodes，每集字段为 number, title, synopsis, dramatic_goal, cliffhanger, characters，编号必须连续。characters 只能使用当前故事设定角色的正式姓名，并只填写本集实际计划登场者。一次只能生成 story_bible 或 episode_outline，另一个字段必须为 null。
修改 episode_outline 时，梗概应说明本集主要事件、冲突转折、登场角色的具体作用与结果；逐集核对 characters 与梗概中的行动人物及前后集承接。不要只补字数、虚构新角色，或机械安排所有角色在每集登场。
如果已有分集，本轮只能调整内容，必须保留每集原始 outline_key、number、duration_seconds 和全部分集数量与顺序，不能新增、删除或排序。不要输出 linked_episode_id 或 synopsis_document；系统会保留关联和未变更的富文本。需要新增续写请告知用户使用续写流程。
Agent 运行边界：{ai_settings.outline_agent_instruction}
剧集结构运行策略：{structure_strategy}
固定 Skill：{skill.name}（{skill.key}）
固定 Skill 指令：{skill.instruction}

入口约定：本轮只讨论或提议故事设定、分集大纲；即使技能包含其他职责也不执行资产提取、媒体生成或正式写入。仅依据下方实际提供的资料，不宣称已读完整项目。输出严格遵守上方 JSON 契约。
用户要求：{message.strip()}
批量角色补全范围（如非null，逐个角色只补targets内授权的空字段；必须保留角色身份和其他全部内容；允许某个字段无法合理补全时留空，episode_outline必须为null）：{json.dumps({k: v for k, v in batch_completion.items() if k != 'story_snapshot'} if batch_completion else None, ensure_ascii=False)}
角色覆盖修订范围（如非null，只允许调整每集characters；必须保留全部分集身份、数量、顺序、标题、梗概、戏剧目标、悬念和时长，story_bible必须为null）：{json.dumps({k: v for k, v in outline_coverage.items() if not k.endswith('_snapshot')} if outline_coverage else None, ensure_ascii=False)}
故事资料页面调整范围（如非null，section=overview时只允许调整title、logline、genre、tone、audience、world、themes；section=events时只允许调整event_timeline并严格遵守剧集结构；characters和其他字段必须原样保留，episode_outline必须为null）：{json.dumps({k: v for k, v in story_adjustment.items() if k != 'story_snapshot'} if story_adjustment else None, ensure_ascii=False)}
当前结构化内容：{json.dumps(latest, ensure_ascii=False)}
用户选择的附件片段：{json.dumps(attachments, ensure_ascii=False)}"""
    if batch_completion:
        story = latest[ARTIFACT_TYPE_STORY_BIBLE]
        selected_characters = [story["characters"][target["character_index"]] for target in batch_completion["targets"]]
        prompt = f"""你是短剧角色设定编辑。只补全指定角色的空字段，只输出一个完整 JSON 对象，不要 Markdown。
格式：{{"reply":"简短说明","story_bible":{{"characters":[{{"character_id":"原角色ID","name":"原姓名","age":"..."}}]}},"episode_outline":null}}。
characters 只返回下列指定角色；每人保留原 name，有 character_id 时原样保留，没有时省略；仅增加其 targets.fields 列出的字段。不得输出完整故事设定、事件或其他角色，不得改名或改已有资料。
age 可用合理年龄段，不编造精确年龄；其他字段写具体、简洁、与故事一致的资料。每个非年龄字段不超过 80 个汉字；无法合理补全时可留空。
用户要求：{message.strip()}
故事背景：{json.dumps({key: story.get(key) for key in ('title', 'logline', 'genre', 'tone', 'world')}, ensure_ascii=False)}
指定角色：{json.dumps(selected_characters, ensure_ascii=False)}
授权补全字段：{json.dumps([{key: target[key] for key in ('character_id', 'name', 'fields')} for target in batch_completion['targets']], ensure_ascii=False)}
用户附件：{json.dumps(attachments, ensure_ascii=False)}"""
    if story_adjustment:
        if story_adjustment["section"] == "overview":
            prompt += "\n本轮是统一的故事调整入口，覆盖上面的局部概览限制及 Skill 讨论指令。立即返回三个简短故事方向，不追问。顶层使用 reply、story_options、recommended_option_index、recommendation_reason、story_bible、episode_outline，后两者为 null。每个方向包含 title、logline、genre、tone、audience、world、themes，以及 direction（一句话核心方向）、highlight（主要亮点）、risk（主要风险）、change_scope（小/中/大），每项控制在600字内。这一步暂不生成角色和事件；用户选择后系统会联动生成完整故事。推荐下标为0到2，推荐理由以用户本次调整要求为最高优先，再考虑因果逻辑、短剧冲突及制作可行性。不是绝对最优，不编造分数。如果用户只要求优化事件，应保持故事方向，只对比三个情节优化方向。"
            prompt += "\ntitle使用真正的剧名，不加‘方向一’等编号。direction、highlight、risk各用一句话，控制在60字以内；recommendation_reason控制在80字以内，不重复三方案的完整梗概。"
        else:
            prompt += "\n本轮必须立即返回完整 story_bible 事件修改稿，episode_outline 为 null。禁止仅返回方向讨论或要求用户再次确认后才生成。只调整 event_timeline，其他内容原样保留。"
    audit_parameters = {
        "agent_workspace": "outline",
        "skill_id": skill.id,
        "skill_key": skill.key,
        "skill_instruction_version": skill.version,
        "source_reference_version": item.updated_at.isoformat(),
        "source_artifacts": source_artifacts,
        "attachment_chunk_ids": selected_chunk_ids,
        "uploaded_attachments": uploaded_metadata,
        **({"character_batch_completion": batch_completion} if batch_completion else {}),
        **({"character_outline_coverage": outline_coverage} if outline_coverage else {}),
        **({"story_adjustment": story_adjustment} if story_adjustment else {}),
        **({"story_options_version": 2} if story_overview_adjustment else {}),
        "story_snapshot": latest.get(ARTIFACT_TYPE_STORY_BIBLE),
        **({"outline_snapshot": latest.get(ARTIFACT_TYPE_EPISODE_OUTLINE), "optimize_all": True} if optimize_all else {}),
        "adjustment_request": message.strip(),
    }
    request_message = await append_message(
        session, item.id, "user", "agent_request", message.strip(), parameters=audit_parameters
    )
    model, route_source = await provider_service.resolve_agent_model(
        session, "outline"
    )
    job = await create_creation_job(
        session,
        item,
        JOB_TARGET_OUTLINE_AGENT,
        prompt,
        "Agent 正在处理上一条消息",
        provider_model_id=model.id,
        parameters=audit_parameters,
    )
    _attach_agent_execution(job, {
        "agent": "outline",
        "surface": "outline_chat",
        "route_source": route_source,
        "provider_model_id": model.id,
        "model_id": model.model_id,
        "instruction": ai_settings.outline_agent_instruction,
        "skill_id": skill.id,
        "skill_key": skill.key,
        "skill_name": skill.name,
        "skill_version": skill.version,
        "skills": [{"instruction": skill.instruction}],
    })
    if batch_completion:
        from app.services.long_form_workflow import configure
        await configure(session, item, job, "characters")
    elif optimize_all:
        if not latest.get(ARTIFACT_TYPE_EPISODE_OUTLINE, {}).get("episodes"):
            raise ConflictError("没有可优化的分集大纲")
        from app.services.long_form_workflow import configure
        await configure(session, item, job, "optimize")
    request_message.job_id = job.id
    await session.flush()
    return job


async def apply_outline_agent_action(
    session: AsyncSession,
    item: CreationSession,
    job_id: int,
    expected_source_version: int,
    actor_id: int,
    selected_character_keys: list[str] | None = None,
    selected_option_index: int | None = None,
    *,
    finalized_result: dict[str, Any] | None = None,
) -> CreationSession:
    from app.services.creation_session_service import JOB_TARGET_OUTLINE_AGENT
    job = await job_service.get_job(session, job_id, actor_id)
    if (
        job.target_type != JOB_TARGET_OUTLINE_AGENT
        or job.target_id != item.id
        or (job.status != "succeeded" and finalized_result is None)
    ):
        raise ConflictError("Agent 提案尚未完成或不属于当前项目")
    job_result = dict(finalized_result if finalized_result is not None else job.result or {})
    preview = dict(job_result.get("action_preview") or {})
    if not preview:
        raise ConflictError("Agent 本轮没有可应用的结构化提案")
    if preview.get("status") == "applied":
        return item
    if preview.get("continuation_job_id"):
        return item
    if preview.get("status") == "rejected":
        raise ConflictError("该 Agent 提案已放弃")
    target_type = str(preview.get("target_type") or "")
    if target_type not in {ARTIFACT_TYPE_STORY_BIBLE, ARTIFACT_TYPE_EPISODE_OUTLINE}:
        raise ConflictError("Agent 提案类型不支持")
    source = dict(preview.get("source") or {})
    source_version = int(source.get("version") or 0)
    if source_version != expected_source_version:
        raise ConflictError("提案源版本校验失败，请刷新后重试")
    current = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == target_type,
            CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    current_version = current.version if current is not None else 0
    current_id = current.id if current is not None else None
    if current_version != source_version or current_id != source.get("id"):
        raise ConflictError("正式内容已产生新版本，请基于最新版本重新生成提案")
    if current is not None and ("revision" not in source or current.revision != source["revision"]):
        raise ConflictError("提案基线已过期或缺少修订号，请基于最新内容重新生成提案")
    proposed = dict(preview.get("proposed") or {})
    if preview.get("options"):
        options = preview["options"]
        if selected_option_index is None or not 0 <= selected_option_index < len(options):
            raise ConflictError("请选择一个新方案")
        from app.services.story_adjustment_workflow import start_story_sync
        await start_story_sync(session, item, job, preview, selected_option_index)
        return item
    if target_type == ARTIFACT_TYPE_STORY_BIBLE:
        from app.services.story_planning_service import (
            restrict_batch_completion,
            restrict_story_section,
        )
        parameters = job.payload.get("parameters") or {}
        batch_completion = parameters.get("character_batch_completion")
        if batch_completion:
            proposed, batch_report = restrict_batch_completion(
                batch_completion, proposed, selected_character_keys
            )
            preview["character_batch"] = {
                **dict(preview.get("character_batch") or {}),
                "items": batch_report,
                "selected_character_keys": selected_character_keys,
            }
        elif parameters.get("story_sync"):
            from app.services.story_adjustment_workflow import validate_synced_story
            proposed = validate_synced_story(parameters, proposed, item.settings)
        elif parameters.get("story_adjustment"):
            proposed = restrict_story_section(
                parameters["story_adjustment"], proposed, item.settings
            )
        elif selected_character_keys is not None:
            raise ConflictError("当前提案不支持逐角色审核")
        proposed = StoryBibleContent.model_validate(proposed).model_dump()
        from app.services.story_character_ecosystem import enrich_story_bible
        episode_count = item.settings.get("episode_count", 10)
        from app.core.creation_limits import episode_count as validate_episode_count
        episode_count = validate_episode_count(episode_count)
        proposed = enrich_story_bible(proposed, episode_count)
        if current is not None:
            from sqlalchemy import update
            locked = await session.execute(update(CreationArtifact).where(
                CreationArtifact.id == current.id, CreationArtifact.revision == current.revision,
            ).values(revision=current.revision + 1).execution_options(synchronize_session=False))
            if locked.rowcount != 1:
                raise ConflictError("故事设定已被修改，请基于最新版本重新生成提案")
        if not batch_completion or item.status in {"draft", "reviewing", "confirmed"}:
            item.status = SESSION_STATUS_REVIEWING
        message = "Agent 提案已应用为新的故事设定草稿。"
    else:
        parameters = job.payload.get("parameters") or {}
        outline_coverage = parameters.get("character_outline_coverage")
        if outline_coverage:
            from app.services.outline_character_coverage import restrict_outline_coverage
            proposed = restrict_outline_coverage(outline_coverage, proposed)
            story_source = dict((parameters.get("source_artifacts") or {}).get(ARTIFACT_TYPE_STORY_BIBLE) or {})
            story_current = await session.scalar(
                select(CreationArtifact).where(
                    CreationArtifact.session_id == item.id,
                    CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
                    CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
                ).order_by(CreationArtifact.version.desc()).limit(1)
            )
            if story_current is None or story_current.id != story_source.get("id") or story_current.revision != story_source.get("revision"):
                raise ConflictError("故事设定已更新，请基于最新角色重新生成覆盖提案")
        proposed = EpisodeOutlineContent.model_validate(proposed).model_dump()
        if current is not None:
            from app.services import outline_management_service
            baseline = await outline_management_service.normalize(session, item, current)
            entries = proposed["episodes"]
            if [entry.get("outline_key") for entry in entries] != [entry["outline_key"] for entry in baseline["episodes"]]:
                raise ConflictError("AI 提案改变了分集身份或顺序，请重新生成；增删排序使用分集管理")
            for entry, before in zip(entries, baseline["episodes"]):
                entry["duration_seconds"] = before.get("duration_seconds")
            proposed = await outline_management_service.merge_edit(session, item, current, proposed)
            if current.status == "draft":
                await outline_management_service.cas_write(session, current, baseline, current.revision)
            else:
                from sqlalchemy import update
                locked = await session.execute(update(CreationArtifact).where(CreationArtifact.id == current.id, CreationArtifact.revision == current.revision).values(revision=current.revision + 1).execution_options(synchronize_session=False))
                if locked.rowcount != 1:
                    raise ConflictError("大纲已变化，请重新生成提案")
        story_for_review = await session.scalar(
            select(CreationArtifact).where(
                CreationArtifact.session_id == item.id,
                CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
                CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
            ).order_by(CreationArtifact.version.desc()).limit(1)
        )
        proposed["structure_review"] = review_for_settings(
            proposed,
            item.settings,
            story=story_for_review.content if story_for_review is not None else None,
        )
        item.status = SESSION_STATUS_OUTLINE_REVIEWING
        message = "Agent 提案已应用为新的分集大纲草稿。"
    artifact = await create_artifact(session, item, job, target_type, proposed, message)
    if target_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
        from app.services.outline_workflow_service import mark_pending
        await mark_pending(session, item, artifact)
    preview.update({
        "status": "applied",
        "applied_artifact_id": artifact.id,
        "applied_version": artifact.version,
    })
    job.result = {**job_result, "action_preview": preview}
    await append_message(
        session,
        item.id,
        "assistant",
        "agent_action_applied",
        f"已确认应用“{preview.get('title') or 'Agent 提案'}”为 V{artifact.version}。",
        job_id=job.id,
        parameters={"artifact_id": artifact.id, "artifact_version": artifact.version},
    )
    await session.flush()
    return item


async def reject_agent_action(
    session: AsyncSession,
    job_id: int,
    actor_id: int,
    project_id: int,
) -> Job:
    job = await job_service.get_job(session, job_id, actor_id)
    if job.project_id != project_id or job.status != "succeeded":
        raise ConflictError("Agent 提案尚未完成或不属于当前项目")
    job_result = dict(job.result or {})
    preview = dict(job_result.get("action_preview") or {})
    if not preview:
        raise ConflictError("当前任务没有可放弃的 Agent 提案")
    if preview.get("status") == "applied":
        raise ConflictError("已应用的 Agent 提案不能放弃，可使用版本历史恢复")
    if preview.get("status") == "syncing":
        raise ConflictError("故事正在联动生成，请在任务中心取消任务；原故事尚未修改")
    if preview.get("status") == "rejected":
        return job
    preview["status"] = "rejected"
    job.result = {**job_result, "action_preview": preview}
    from app.services.creation_session_service import JOB_TARGET_OUTLINE_AGENT
    if job.target_type == JOB_TARGET_OUTLINE_AGENT and job.target_id is not None:
        await append_message(
            session,
            job.target_id,
            "assistant",
            "agent_action_rejected",
            f"已放弃“{preview.get('title') or 'Agent 提案'}”，正式内容未改变。",
            job_id=job.id,
        )
    await session.flush()
    return job


# 需要从原文件导入的辅助函数
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
)
