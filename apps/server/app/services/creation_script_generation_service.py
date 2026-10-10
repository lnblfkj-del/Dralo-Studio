"""Batch episode-script generation and AI optimization proposals."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_STORY_BIBLE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_TYPE_TEXT,
    CreationArtifact,
    CreationSession,
    Episode,
    Job,
    Project,
    Provider,
    StoryContinuityFact,
)
from app.models.continuity import CONTINUITY_STATUS_ACTIVE
from app.schemas.creation import EpisodeScriptContent
from app.services import (
    job_service,
    project_service,
    script_version_service,
    text_model_policy_service,
)
from app.services.creation_agent_service import _attach_agent_execution, _resolve_agent_execution
from app.services.creation_session_service import (
    JOB_TARGET_EPISODE_SCRIPT_BATCH,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    SESSION_STATUS_OUTLINE_CONFIRMED,
    SESSION_STATUS_SCRIPT_GENERATING,
    SESSION_STATUS_SCRIPT_REVIEWING,
    get_artifact,
)
from app.services.episode_duration_policy import episode_duration_guidance
from app.services.episode_generation_context import (
    episode_generation_context,
    episode_generation_instruction,
    known_character_names,
)
from app.services.episode_script_structure_review import build_structure_review_context
from app.services.narrative_spec_service import narrative_spec_from_settings
from app.services.outline_workflow_service import episode_context, require_episode_context

SCRIPT_BATCH_ACTIVE_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
}

SCREENPLAY_PACING_POLICY_VERSION = "screenplay-pacing-v3"

def episode_script_length_limits(
    duration_seconds: int | None, *, genre: str = ""
) -> dict[str, int] | None:
    """Return editorial targets; episode runtime is an estimate, not a save limit."""
    seconds = int(duration_seconds or 0)
    if seconds < 1:
        return None
    minimum_chars = max(120, seconds * 7)
    maximum_chars = max(minimum_chars + 60, seconds * 9)
    cultivation_reversal = "修仙" in genre and "穿越" in genre
    return {
        "seconds": seconds,
        "minimum_chars": minimum_chars,
        "maximum_chars": maximum_chars,
        "dialogue_chars": max(60, seconds * (3 if cultivation_reversal else 4)),
        "maximum_scenes": 2 if seconds <= 45 else 3 if seconds <= 90 else 5,
    }


def episode_script_length_guidance(
    duration_seconds: int | None, *, genre: str = ""
) -> str:
    """Translate an approximate runtime into non-blocking editorial guidance."""
    limits = episode_script_length_limits(duration_seconds, genre=genre)
    if limits is None:
        return "本集目标时长未设置；保持篇幅克制，不要用冗长动作描写填充正文。"
    return (
        episode_duration_guidance(limits["seconds"])
        + f"正文（含场景、动作、对白）可参考 {limits['minimum_chars']}-{limits['maximum_chars']} 字；"
        f"可朗读对白可参考约 {limits['dialogue_chars']} 字，"
        f"场景可参考 {limits['maximum_scenes']} 个。"
        "这些是审阅提示而非截断或保存上限；优先保证已确认剧情、人物动机和可表演的行动/反应。"
        "第一处可见变化、主要冲突和当集回报应清晰；仅在已确认大纲要求时留集尾钩子。"
        "对白宜短且标明说话人，给动作和情绪反应留时间；不为了凑字数增加空镜、重复台词或固定次数的反转。"
    )


def repair_episode_script_result(value: object) -> object:
    """Normalize common structured screenplay variants without inventing content."""
    if not isinstance(value, dict):
        return value
    repaired = dict(value)
    script = repaired.get("script")
    if isinstance(script, list):
        lines: list[str] = []
        for index, scene in enumerate(script, start=1):
            if not isinstance(scene, dict):
                lines.append(str(scene))
                continue
            setting = str(scene.get("setting") or scene.get("scene") or f"场景 {index}").strip()
            lines.append(f"【场{index}】{setting}")
            action = scene.get("action")
            if isinstance(action, list):
                lines.extend(f"△ {str(item).strip()}" for item in action if str(item).strip())
            elif str(action or "").strip():
                lines.append(f"△ {str(action).strip()}")
            dialogue = scene.get("dialogue")
            if isinstance(dialogue, list):
                for item in dialogue:
                    if isinstance(item, dict):
                        character = str(item.get("character") or item.get("speaker") or "").strip()
                        line = str(item.get("line") or item.get("text") or "").strip()
                        if line:
                            lines.append(f"{character}：{line}" if character else line)
                    elif str(item).strip():
                        lines.append(str(item).strip())
        repaired["script"] = "\n".join(lines).strip()

    continuity = repaired.get("continuity_update")
    if isinstance(continuity, dict):
        normalized = dict(continuity)
        for key in (
            "character_changes",
            "prop_changes",
            "resolved_hooks",
            "new_hooks",
        ):
            entries = normalized.get(key)
            if isinstance(entries, dict):
                normalized[key] = [
                    f"{name}：{description}" for name, description in entries.items()
                ]
        repaired["continuity_update"] = normalized
    return repaired


def _artifact_source(artifact: CreationArtifact) -> dict[str, int]:
    return {
        "artifact_id": artifact.id,
        "version": artifact.version,
        "revision": artifact.revision,
    }


async def _validate_artifact_source(
    session: AsyncSession,
    item: CreationSession,
    artifact_type: str,
    source: dict,
    label: str,
) -> None:
    if not source:
        return
    current = await get_artifact(
        session, item, artifact_type, status=ARTIFACT_STATUS_CONFIRMED
    )
    expected = (
        int(source.get("artifact_id") or 0),
        int(source.get("version") or 0),
        int(source.get("revision") or 0),
    )
    actual = (current.id, current.version, current.revision)
    if actual != expected:
        raise ConflictError(f"{label}已更新，本批旧正文结果不能继续写入，请重新生成")
    latest_active = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == artifact_type,
            CreationArtifact.status.in_({
                ARTIFACT_STATUS_CONFIRMED,
                ARTIFACT_STATUS_DRAFT,
            }),
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    if latest_active is not None and latest_active.id != current.id:
        raise ConflictError(
            f"{label}存在待确认新版本，本批旧正文结果不能继续写入，请先完成来源审核"
        )


async def validate_episode_script_parent_sources(
    session: AsyncSession,
    parent: Job,
    *,
    require_active: bool,
) -> CreationSession:
    if parent.target_type != JOB_TARGET_EPISODE_SCRIPT_BATCH or parent.target_id is None:
        raise ConflictError("分集正文批次来源无效")
    if require_active and parent.status not in SCRIPT_BATCH_ACTIVE_STATUSES:
        raise ConflictError("分集正文批次已结束或取消，旧结果不能继续写入")
    item = await session.get(CreationSession, parent.target_id)
    if item is None or item.owner_id != parent.owner_id:
        raise ConflictError("创作会话不可用")
    payload = dict(parent.payload or {})
    await _validate_artifact_source(
        session,
        item,
        ARTIFACT_TYPE_STORY_BIBLE,
        dict(payload.get("story_source") or {}),
        "故事设定",
    )
    await _validate_artifact_source(
        session,
        item,
        ARTIFACT_TYPE_EPISODE_OUTLINE,
        dict(payload.get("outline_source") or {}),
        "分集大纲",
    )
    return item


async def validate_episode_script_job_sources(
    session: AsyncSession,
    job: Job,
    *,
    require_parent_active: bool,
) -> Episode:
    if job.target_type != JOB_TARGET_EPISODE_SCRIPT_GENERATION or job.target_id is None:
        raise ConflictError("分集正文任务来源无效")
    episode = await session.get(Episode, job.target_id)
    if episode is None or episode.owner_id != job.owner_id:
        raise ConflictError("分集不存在")
    parameters = dict((job.payload or {}).get("parameters") or {})
    if job.parent_job_id is not None:
        parent = await session.get(Job, job.parent_job_id)
        if parent is None:
            raise ConflictError("分集正文批次不存在")
        await validate_episode_script_parent_sources(
            session, parent, require_active=require_parent_active
        )
    await require_episode_context(
        session, episode, parameters.get("source_episode_context")
    )
    expected_revision = int(parameters.get("source_revision", episode.script_revision))
    if episode.script_revision != expected_revision:
        raise ConflictError("本集剧本已更新，生成结果未覆盖现有版本")
    for dependency in list(parameters.get("dependency_episode_revisions") or []):
        if not isinstance(dependency, dict):
            continue
        dependency_id = int(dependency.get("episode_id") or 0)
        expected = int(dependency.get("script_revision") or 0)
        source_episode = await session.get(Episode, dependency_id)
        if (
            source_episode is None
            or source_episode.project_id != episode.project_id
            or source_episode.script_revision != expected
        ):
            number = int(dependency.get("episode_number") or 0)
            raise ConflictError(
                f"第 {number} 集承接正文已更新，本集旧生成结果不能写入，请重新生成"
            )
    return episode


async def _build_episode_generation_prompt(
    session: AsyncSession,
    *,
    parent: Job,
    episode: Episode,
    preceding_update: dict | None,
) -> tuple[str, dict]:
    payload = dict(parent.payload or {})
    bible = dict(payload.get("story_bible") or {})
    outline_entries = list((payload.get("episode_outline") or {}).get("episodes") or [])
    outline_by_number = {int(entry["number"]): entry for entry in outline_entries}
    outline_entry = outline_by_number.get(episode.number)
    if outline_entry is None:
        raise ConflictError(f"第 {episode.number} 集缺少已确认大纲")

    generation_context = episode_generation_context(
        dict(payload.get("narrative_spec") or {}), episode.number
    )
    requires_previous = bool(generation_context["requires_previous"])
    previous = await session.scalar(select(Episode).where(
        Episode.project_id == episode.project_id,
        Episode.number == episode.number - 1,
    )) if requires_previous and episode.number > 1 else None
    if requires_previous and episode.number > 1 and (
        previous is None or not (previous.script or "").strip()
    ):
        raise ConflictError(f"第 {episode.number} 集生成前必须先完成第 {episode.number - 1} 集正文")

    recent = []
    if generation_context["include_recent_continuity"]:
        recent_conditions = [
            Episode.project_id == episode.project_id,
            Episode.number < episode.number - 1,
        ]
        unit = generation_context.get("unit")
        if isinstance(unit, dict):
            recent_conditions.append(
                Episode.number >= int(unit.get("episode_start") or episode.number)
            )
        recent = list((await session.scalars(
            select(Episode).where(*recent_conditions)
            .order_by(Episode.number.desc()).limit(2)
        )).all())
    recent.reverse()
    recent_summaries = [
        {
            "episode_number": item.number,
            "title": item.title,
            "synopsis": item.synopsis,
            "script_revision": item.script_revision,
            "ending_excerpt": (item.script or "")[-1800:],
        }
        for item in recent
    ]
    recent_ids = [item.id for item in recent]
    facts = list((await session.scalars(select(StoryContinuityFact).join(
        Episode, Episode.id == StoryContinuityFact.source_episode_id,
    ).where(
        StoryContinuityFact.project_id == episode.project_id,
        Episode.number < episode.number,
        Episode.script_revision == StoryContinuityFact.source_revision,
        Episode.number >= int((generation_context.get("unit") or {}).get("episode_start") or 1),
        generation_context["include_recent_continuity"],
        StoryContinuityFact.status == CONTINUITY_STATUS_ACTIVE,
    ).order_by(Episode.number, StoryContinuityFact.id))).all())
    facts = [fact for fact in facts if fact.source_episode_id in recent_ids
             or fact.fact_type in {"open_thread", "generated_new_hooks", "generated_resolved_hooks",
                                   "generated_character_changes", "generated_prop_changes"}]
    from app.services.script_continuity_context import compact_continuity_history
    continuity_facts, history_coverage = compact_continuity_history(
        facts, recent_ids=set(recent_ids + ([previous.id] if previous else [])),
        current_names=outline_entry.get("characters", []),
        outline_text=json.dumps(outline_entry, ensure_ascii=False),
    )
    unit = generation_context.get("unit")
    unit_start = int((unit or {}).get("episode_start") or 1)
    unit_end = int((unit or {}).get("episode_end") or len(outline_entries))
    include_neighbors = generation_context["structure"] in {"continuous", "hybrid", "unit"}
    neighbors = {
        "previous_outline": outline_by_number.get(episode.number - 1)
        if include_neighbors and episode.number > unit_start else None,
        "current_outline": outline_entry,
        "next_outline": outline_by_number.get(episode.number + 1)
        if include_neighbors and episode.number < unit_end else None,
    }
    handoff = {
        "previous_actual_script": (previous.script if previous else ""),
        "previous_script_revision": (previous.script_revision if previous else None),
        "previous_continuity_update": preceding_update or {} if requires_previous else {},
        "recent_episode_handoffs": recent_summaries,
        "active_continuity_facts": continuity_facts,
        "history_coverage": history_coverage,
    }
    dependencies = ([previous] if previous is not None else []) + recent
    existing_ids = {item.id for item in dependencies}
    fact_episode_ids = {fact.source_episode_id for fact in facts} - existing_ids
    if fact_episode_ids:
        dependencies += list((await session.scalars(select(Episode).where(Episode.id.in_(fact_episode_ids)))).all())
    from app.services.long_form_context import checked_prompt, story_context
    scoped_bible = story_context(bible, episode.number, episode.number, names=outline_entry.get("characters", []))
    instruction_prefix = str(payload.get("instruction_prefix") or "")
    schema = EpisodeScriptContent.model_json_schema()
    target_duration = episode.duration_estimate or int(payload.get("episode_duration") or 0)
    genre = str(payload.get("project_genre") or "")
    length_guidance = episode_script_length_guidance(target_duration, genre=genre)
    structure_instruction = episode_generation_instruction(generation_context)
    prompt = f"""{instruction_prefix}

你是专业短剧编剧。首字符必须是 {{，末字符必须是 }}；只输出符合给定 Schema 的 JSON，禁止输出分析、草稿、解释或 Markdown。
叙事结构规则：{structure_instruction}
人物姓名、身份、关系和已确认的长期事实必须与全剧设定一致；不得依赖模型会话、渠道账号或未提供的记忆。
script 使用中文剧本格式，明确场景、时间、人物、动作和对白，不输出分镜。按剧情需要保留声音与配乐建议，剧情内音乐明确标注。落实本集目标；只在已确认大纲要求时设置集尾钩子，不得强行添加跨集承接。
continuity_update 与正文必须在同一次返回中给出：start_state 为本集承接状态，end_state 为本集结束状态，并列出人物/道具变化、已回收与新产生的悬念。
长期记录按来源集与版本追溯：generated_* 为模型从当集正文报告的状态变化，并非人工确认事实。结合后续 resolved_hooks 判断早期 new_hooks 是否已回收，不得把已回收伏笔再次当成悬而未决；不确定时保留疑问，不编造解决过程。
history_coverage说明历史覆盖范围。检索记录不是完整当前状态，未检索到不代表未发生；同一人物的新变化不自动撤销旧能力或关系。优先遵守已确认设定、上一集实际正文和有来源的明确事实，不能把历史悬念重新开场。
字段类型硬约束：script 必须是单个字符串，不得是场景数组；continuity_update 的 character_changes、prop_changes、resolved_hooks、new_hooks 必须是字符串数组，不得是键值对象。

输出 Schema：{json.dumps(schema, ensure_ascii=False)}
已确认设定中与本集相关的规划（不是已发生事实）：{json.dumps(scoped_bible, ensure_ascii=False)}

本次任务：按已确认叙事结构生成第 {episode.number} 集完整可拍摄正文，episode_number 必须为 {episode.number}。
时长与节奏建议（非硬上限）：{length_guidance}
题材：{(payload.get('project_genre') or '以已确认全剧设定为准')!s}。视觉风格标识：{(payload.get('style_id') or 'default')!s}。视觉风格不改变故事题材，也不自行把真人改写成动画或反之。
相邻大纲：{json.dumps(neighbors, ensure_ascii=False)}
实际剧情交接：{json.dumps(handoff, ensure_ascii=False)}"""
    return checked_prompt(prompt), {
        "source_revision": episode.script_revision,
        "source_episode_context": episode_context(episode),
        "known_character_names": known_character_names(bible),
        "screenplay_characters": list(bible.get("characters") or []),
        "screenplay_format_version": (payload.get("agent_execution") or {}).get("screenplay_format_version"),
        "target_duration_seconds": target_duration or None,
        "script_length_guidance": length_guidance,
        "script_length_budget": episode_script_length_limits(target_duration, genre=genre),
        "screenplay_pacing_policy_version": SCREENPLAY_PACING_POLICY_VERSION,
        "narrative_structure": generation_context["structure"],
        "narrative_unit": generation_context.get("unit"),
        "requires_previous_episode": requires_previous,
        "episode_closed": generation_context["episode_closed"],
        "context_strategy": generation_context["context_strategy"],
        "structure_review_context": build_structure_review_context(generation_context, preceding_update, outline_entry),
        "story_source": dict(payload.get("story_source") or {}),
        "outline_source": dict(payload.get("outline_source") or {}),
        "dependency_episode_revisions": [
            {
                "episode_id": item.id,
                "episode_number": item.number,
                "script_revision": item.script_revision,
            }
            for item in dependencies
        ],
    }

async def _queue_episode_script_child(
    session: AsyncSession,
    *,
    parent: Job,
    item: CreationSession,
    position: int,
    preceding_update: dict | None = None,
) -> Job:
    numbers = [int(value) for value in (parent.payload or {}).get("episode_numbers", [])]
    number = numbers[position]
    episode = await session.scalar(select(Episode).where(
        Episode.project_id == item.project_id,
        Episode.number == number,
    ))
    if episode is None:
        raise ConflictError(f"第 {number} 集不存在")
    prompt, source = await _build_episode_generation_prompt(
        session, parent=parent, episode=episode, preceding_update=preceding_update
    )
    child = await job_service.create_text_job(
        session,
        item.owner_id,
        provider_model_id=int((parent.payload or {})["provider_model_id"]),
        prompt=prompt,
        project_id=item.project_id,
        parameters={
            "session_id": item.id,
            "episode_number": number,
            "batch_position": position,
            "batch_parent_id": parent.id,
            **source,
        },
    )
    child.target_type = JOB_TARGET_EPISODE_SCRIPT_GENERATION
    child.target_id = episode.id
    child.parent_job_id = parent.id
    text_model_policy_service.inherit_job_snapshot(parent, child)
    # A timeout after provider acceptance may already be billable. Never replay
    # screenplay generation automatically; an explicit batch retry is auditable.
    child.max_attempts = 1
    _attach_agent_execution(child, dict((parent.payload or {}).get("agent_execution") or {}))
    await session.flush()
    return child


async def queue_next_episode_script_job(
    session: AsyncSession,
    workflow_session: CreationSession,
    completed_job: Job,
    continuity_update: dict,
) -> Job | None:
    """Create only the next dependent child after the current child was applied."""

    if completed_job.parent_job_id is None:
        return None
    parent = await session.get(Job, completed_job.parent_job_id)
    if parent is None or parent.target_type != JOB_TARGET_EPISODE_SCRIPT_BATCH:
        return None
    await validate_episode_script_parent_sources(session, parent, require_active=True)
    position = int(dict(completed_job.payload.get("parameters") or {}).get("batch_position", -1))
    next_position = position + 1
    numbers = list((parent.payload or {}).get("episode_numbers") or [])
    if next_position >= len(numbers):
        return None
    next_episode = await session.scalar(select(Episode).where(
        Episode.project_id == workflow_session.project_id,
        Episode.number == int(numbers[next_position]),
    ))
    existing = await session.scalar(select(Job.id).where(
        Job.parent_job_id == parent.id,
        Job.target_type == JOB_TARGET_EPISODE_SCRIPT_GENERATION,
        Job.target_id == (next_episode.id if next_episode is not None else -1),
    ))
    if existing is not None:
        return None
    return await _queue_episode_script_child(
        session,
        parent=parent,
        item=workflow_session,
        position=next_position,
        preceding_update=continuity_update,
    )


async def create_episode_script_generation_jobs(
    session: AsyncSession,
    item: CreationSession,
    episode_numbers: list[int],
    overwrite: bool,
) -> list[Job]:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    active_batch = await session.scalar(select(Job.id).where(
        Job.owner_id == item.owner_id,
        Job.target_type == JOB_TARGET_EPISODE_SCRIPT_BATCH,
        Job.target_id == item.id,
        Job.status.in_([
            JOB_STATUS_QUEUED,
            JOB_STATUS_RUNNING,
            JOB_STATUS_PROCESSING,
            JOB_STATUS_RETRYING,
        ]),
    ))
    if active_batch is not None:
        raise ConflictError("当前已有一批分集正文按顺序生成")
    # A worker lease can expire after the parent batch has already become
    # terminal. Older projects may therefore remain at script_generating even
    # though no active batch exists. The confirmed artifacts below are the
    # authoritative generation prerequisites; repair only this stale state.
    if item.status == SESSION_STATUS_SCRIPT_GENERATING:
        item.status = SESSION_STATUS_SCRIPT_REVIEWING
    if item.status not in {SESSION_STATUS_OUTLINE_CONFIRMED, SESSION_STATUS_SCRIPT_REVIEWING}:
        raise ConflictError("请先确认分集大纲")
    bible = await get_artifact(
        session, item, ARTIFACT_TYPE_STORY_BIBLE, status=ARTIFACT_STATUS_CONFIRMED
    )
    outline = await get_artifact(
        session, item, ARTIFACT_TYPE_EPISODE_OUTLINE, status=ARTIFACT_STATUS_CONFIRMED
    )
    episodes = await project_service.list_episodes(session, item.project_id)
    by_number = {episode.number: episode for episode in episodes}
    requested = sorted(set(episode_numbers or sorted(by_number)))
    missing = [number for number in requested if number not in by_number]
    if missing:
        raise ConflictError(f"分集不存在：{missing}")
    outline_by_number = {entry["number"]: entry for entry in outline.content["episodes"]}
    eligible: list[int] = []
    for number in requested:
        episode = by_number[number]
        if (episode.script or "").strip() and not overwrite:
            continue
        active = await session.scalar(select(Job.id).where(
            Job.owner_id == item.owner_id,
            Job.target_type == JOB_TARGET_EPISODE_SCRIPT_GENERATION,
            Job.target_id == episode.id,
            Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING]),
        ))
        if active is not None:
            continue
        if number not in outline_by_number:
            raise ConflictError(f"第 {number} 集缺少已确认大纲")
        eligible.append(number)
    if not eligible:
        raise ConflictError("所选分集已有正文或正在生成")
    eligible_set = set(eligible)
    narrative_spec = narrative_spec_from_settings(item.settings)
    project = await session.get(Project, item.project_id)
    for number in eligible:
        if number <= 1:
            continue
        generation_context = episode_generation_context(narrative_spec, number)
        if not generation_context["requires_previous"]:
            continue
        previous = by_number.get(number - 1)
        if (previous is None or not (previous.script or "").strip()) and number - 1 not in eligible_set:
            raise ConflictError(f"第 {number} 集生成前必须先完成第 {number - 1} 集正文")

    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "episode_script_generation"
    )
    provider = await session.get(Provider, model.provider_id)
    if provider is None:
        raise ConflictError("剧本生成模型渠道不存在")
    from app.services import execution_policy_service, text_model_policy_service

    text_model_policy_service.resolve(
        model, provider, {}, execution_policy_service.current_snapshot()
    )
    parent = Job(
        owner_id=item.owner_id,
        project_id=item.project_id,
        provider_id=model.provider_id,
        job_type=JOB_TYPE_TEXT,
        status=JOB_STATUS_PROCESSING,
        target_type=JOB_TARGET_EPISODE_SCRIPT_BATCH,
        target_id=item.id,
        progress=0,
        provider=provider.name,
        model=model.model_id,
        cost_estimate=0,
        payload={
            "provider_model_id": model.id,
            "episode_numbers": eligible,
            "expected_total": len(eligible),
            "overwrite": overwrite,
            "instruction_prefix": instruction_prefix,
            "agent_execution": execution,
            "story_bible": bible.content,
            "episode_outline": outline.content,
            "story_source": _artifact_source(bible),
            "outline_source": _artifact_source(outline),
            "episode_duration": int((item.settings or {}).get("episode_duration") or 0),
            "project_genre": project.genre if project is not None else None,
            "style_id": str((item.settings or {}).get("style_id") or "default"),
            "screenplay_pacing_policy_version": SCREENPLAY_PACING_POLICY_VERSION,
            "narrative_spec": narrative_spec,
        },
        result={"total": len(eligible), "completed": 0, "succeeded": 0, "failed": 0, "cancelled": 0},
    )
    session.add(parent)
    await session.flush()
    await _queue_episode_script_child(
        session, parent=parent, item=item, position=0
    )
    item.status = SESSION_STATUS_SCRIPT_GENERATING
    await session.flush()
    return [parent]


async def create_episode_script_optimization_job(
    session: AsyncSession,
    episode: Episode,
    instruction: str,
) -> Job:
    await require_episode_context(session, episode, episode_context(episode))
    active = await session.scalar(select(Job.id).where(
        Job.owner_id == episode.owner_id,
        Job.target_type == JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
        Job.target_id == episode.id,
        Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING]),
    ))
    if active is not None:
        raise ConflictError("本集剧本正在优化")
    source = (episode.script or "").strip()
    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "episode_script_optimization"
    )
    from app.services.screenplay_preflight import project_catalog
    source_catalog = await project_catalog(session, episode.project_id)
    task = "优化" if source else "创作"
    prompt = f"""{instruction_prefix}

你是短剧编剧。根据用户要求{task}单集剧本，保留既有核心剧情和人物关系，不得输出分镜。
严格输出单个 JSON 对象，不要 Markdown、代码围栏或解释文字：
{{"reply":"优化说明","title":"","synopsis":"","script":""}}
script 必须是完整、可编辑的中文剧本正文；title 和 synopsis 必须与优化后的正文一致。按剧情需要保留声音与配乐建议，剧情内音乐明确标注。
用户要求：{instruction.strip() or ("优化节奏、人物动机和对白，保留核心剧情。" if source else "根据标题与摘要创作完整可拍摄正文。")}
当前集号：{episode.number}
当前标题：{episode.title or ""}
当前摘要：{episode.synopsis or ""}
当前正文：{source or "尚无正文，请根据标题与摘要创作。"}"""
    job = await job_service.create_text_job(
        session,
        episode.owner_id,
        provider_model_id=model.id,
        prompt=prompt,
        project_id=episode.project_id,
        parameters={
            "agent_workspace": "episode_script_optimization",
            "screenplay_format_version": execution.get("screenplay_format_version"),
            "screenplay_source_catalog": source_catalog,
            "source_revision": episode.script_revision,
            "source_script": episode.script or "",
            "source_episode_context": episode_context(episode),
            "instruction": instruction.strip(),
        },
    )
    job.target_type = JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION
    job.target_id = episode.id
    job.max_attempts = 1
    _attach_agent_execution(job, execution)
    await session.flush()
    return job


async def apply_episode_script_optimization(
    session: AsyncSession,
    episode: Episode,
    job_id: int,
    expected_revision: int,
    actor_id: int,
) -> Episode:
    job = await job_service.get_job(session, job_id, actor_id)
    if (
        job.target_type != JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION
        or job.target_id != episode.id
        or job.status != "succeeded"
    ):
        raise ConflictError("AI 优化结果尚未完成或不属于本集")
    job_result = dict(job.result or {})
    preview = dict(job_result.get("action_preview") or {})
    if preview.get("status") == "applied":
        return episode
    if preview.get("status") == "rejected":
        raise ConflictError("该 AI 优化提案已放弃")
    await require_episode_context(session, episode, dict(job.payload.get("parameters") or {}).get("source_episode_context"))
    proposal = dict(job_result.get("proposal") or {})
    if not proposal:
        raise ConflictError("AI 优化结果不存在")
    source_revision = int(proposal.get("source_revision", -1))
    if source_revision != expected_revision or episode.script_revision != expected_revision:
        raise ConflictError("剧本已更新，请基于最新版本重新执行 AI 优化")
    updated = await script_version_service.save_script(
        session,
        episode,
        str(proposal["script"]),
        expected=expected_revision,
        actor_id=actor_id,
        note=str(proposal.get("reply") or "AI 优化"),
        source="ai",
    )
    updated.title = str(proposal.get("title") or updated.title or "")
    updated.synopsis = str(proposal.get("synopsis") or updated.synopsis or "")
    from app.services.story_continuity_service import sync_episode_continuity_records
    await sync_episode_continuity_records(
        session, updated, confirmation_status="draft"
    )
    preview.update({
        "status": "applied",
        "applied_revision": updated.script_revision,
    })
    job.result = {
        **job_result,
        "applied_revision": updated.script_revision,
        "action_preview": preview,
    }
    await session.flush()
    return updated
