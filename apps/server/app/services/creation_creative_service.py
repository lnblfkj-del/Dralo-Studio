"""M3 创意方向业务逻辑：创作方向提案、规格更新与故事确认。

本模块处理从用户创意到结构化故事设定的前期引导流程。
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    CreationSession,
    Job,
    Project,
)
from app.services.creation_session_service import append_message
from app.services.creation_agent_service import create_creation_job
from app.services.creation_story_service import create_story_bible_job
from app.services.story_direction_proposals import understanding_fingerprint
from app.services.story_character_ecosystem import character_ecosystem_prompt
from app.services.creation_asset_scope import (
    rebind_asset_breakdown_state,
    rebind_project_asset_scopes,
)
from app.services.narrative_spec_service import (
    narrative_spec_from_settings,
    narrative_spec_impact,
    normalize_narrative_spec,
    with_narrative_spec,
)
from app.services.narrative_prompt_service import story_bible_strategy_prompt

JOB_TARGET_CREATIVE_DIRECTION = "creative_direction"
ACTIVE_JOB_STATUSES = {"queued", "running", "processing", "retrying"}


CREATIVE_DIRECTION_OPTIONS = {
    "recommended": {
        "title": "按原创意继续",
        "description": "根据原始创意判断题材、人物关系、核心冲突与集尾钩子，不预设固定类型。",
    },
    "character": {
        "title": "人物关系推进",
        "description": "把人物目标、关系变化和信任重建作为连续剧情的主要推动力。",
    },
    "conflict": {
        "title": "冲突与博弈",
        "description": "提高危机密度与反转频率，让每集结尾都形成明确追看悬念。",
    },
}


def _resolve_creative_direction(selected_option: str) -> dict[str, str]:
    option = CREATIVE_DIRECTION_OPTIONS.get(selected_option)
    if option is not None:
        return {
            "value": selected_option,
            "title": option["title"],
            "description": option["description"],
        }

    cleaned = selected_option.strip()
    if not cleaned:
        raise ConflictError("创作方向不能为空")
    return {
        "value": cleaned,
        "title": cleaned,
        "description": f"用户自定义方向：{cleaned}",
    }


async def submit_creative_direction(
    session: AsyncSession,
    item: CreationSession,
    selected_option: str,
    extra_requirements: str,
    proposal: dict[str, Any] | None = None,
    proposal_fingerprint: str | None = None,
) -> CreationSession:
    direction = _resolve_creative_direction(selected_option.strip())
    if proposal is not None:
        # 快照是选中方案内容的唯一来源；标题必须与快照同源，
        # 否则会出现“旧标题配新内容”。
        direction = {
            "value": direction["value"],
            "title": str(proposal.get("title") or direction["title"]),
            "description": str(proposal.get("difference") or direction["description"]),
        }
    settings = dict(item.settings)
    workflow = dict(settings.get("creative_workflow") or {})
    proposal_batch = dict(workflow.get("direction_proposals") or {})
    if proposal is not None and proposal_batch:
        if proposal_fingerprint != proposal_batch.get("fingerprint"):
            raise ConflictError("候选方案已过期，请按当前故事理解重新生成")
        stored = next((entry for entry in proposal_batch.get("proposals", []) if entry.get("id") == proposal.get("id")), None)
        if stored is None or stored != proposal:
            raise ConflictError("候选方案快照与服务器版本不一致，请刷新后重试")
    workflow.update({
        "stage": "awaiting_story_confirmation",
        "selected_option": direction["value"],
        "selected_title": direction["title"],
        "extra_requirements": extra_requirements.strip(),
    })
    # 重新选择时必须清除旧快照，避免留着上一次的方案内容。
    if proposal is not None:
        workflow["selected_proposal"] = dict(proposal)
        workflow["selected_proposal_fingerprint"] = proposal_fingerprint
        workflow["selected_proposal_version"] = proposal_batch.get("version")
        workflow["selected_input_snapshot"] = proposal_batch.get("input_snapshot")
    else:
        workflow.pop("selected_proposal", None)
        workflow.pop("selected_proposal_fingerprint", None)
        workflow.pop("selected_proposal_version", None)
        workflow.pop("selected_input_snapshot", None)
    settings["creative_workflow"] = workflow
    item.settings = settings
    await append_message(
        session,
        item.id,
        "user",
        "direction_selection",
        direction["title"],
        parameters={
            "selected_option": direction["value"],
            "selected_title": direction["title"],
            "extra_requirements": extra_requirements.strip(),
        },
    )
    await append_message(
        session,
        item.id,
        "assistant",
        "story_confirmation",
        "已整理创作方向。确认后将写入故事设定，并生成可编辑的结构化内容。",
        parameters={
            "selected_option": direction["value"],
            "selected_title": direction["title"],
            "description": direction["description"],
            "extra_requirements": extra_requirements.strip(),
        },
    )
    await session.flush()
    return item


async def create_creative_direction_job(
    session: AsyncSession,
    item: CreationSession,
    *,
    genre: str = "",
    conflict: str = "",
    characters: str = "",
    tone: str = "",
) -> Job:
    """Run the professional direction Skill once and persist an auditable request."""
    inputs = {
        "genre": genre.strip(),
        "conflict": conflict.strip(),
        "characters": characters.strip(),
        "tone": tone.strip(),
    }
    fingerprint = understanding_fingerprint(brief=item.brief, **inputs)
    active = await session.scalar(select(Job).where(
        Job.owner_id == item.owner_id,
        Job.target_type == JOB_TARGET_CREATIVE_DIRECTION,
        Job.target_id == item.id,
        Job.status.in_(ACTIVE_JOB_STATUSES),
    ).order_by(Job.id.desc()).limit(1))
    if active is not None:
        active_fingerprint = (active.payload.get("parameters") or {}).get("input_fingerprint")
        if active_fingerprint == fingerprint:
            return active
        raise ConflictError("上一版故事方向仍在生成，请等待完成后再提交新理解")

    workflow = dict((item.settings or {}).get("creative_workflow") or {})
    previous = dict(workflow.get("direction_proposals") or {})
    version = int(previous.get("version") or 0) + 1
    prompt = f"""你是专业短剧与电影前期策划。分析用户原始创意与可编辑理解，只输出 JSON，不要 Markdown。
顶层必须且只能包含 understanding、proposals。
understanding 必须包含 genre、conflict、characters、tone、audience；每项必须具体来自输入或清楚标明的专业推导，禁止套用固定喜剧、悬疑或证据链模板。characters 可写现有人物关系与支撑长篇所需角色槽位，最多 1200 字。
proposals 必须恰好 3 项。每项只能包含 id、title、spine、relationships、difference。
title 为 2~24 个汉字或等价长度的精炼方向名，不能直接截取整段原始创意；三个标题不得同义改写。
spine 说明可持续的故事发动机、行动与代价；relationships 说明人物关系怎样改变剧情；difference 必须明确本方案与另外两案在叙事重心和观众体验上的实质区别。
三个方案要共享故事核心但形成不同策略，不得只替换形容词。不得生成 Story Bible、分集大纲或剧本正文。
understanding.characters 不应只复述用户点名的人物；还要概括现有人物关系，并指出为支撑项目集数所需的核心、常驻、阶段或功能角色槽位。角色槽位是职责建议，不要擅自补成人名。
原始创意：{item.brief}
用户可编辑理解：{json.dumps(inputs, ensure_ascii=False)}
项目规格：{json.dumps({"episode_count": item.settings.get("episode_count"), "episode_duration": item.settings.get("episode_duration"), "market": item.settings.get("market")}, ensure_ascii=False)}"""
    parameters = {
        "input_fingerprint": fingerprint,
        "input_snapshot": {"brief": item.brief, **inputs},
        "proposal_version": version,
    }
    job = await create_creation_job(
        session, item, JOB_TARGET_CREATIVE_DIRECTION, prompt,
        "故事方向正在生成，请勿重复提交",
        parameters=parameters, agent_key="outline", execution_surface="creative_direction",
    )
    # A malformed structured result needs human-visible feedback. Retrying the
    # exact same planning prompt would only spend quota without changing input.
    job.max_attempts = 1
    settings = dict(item.settings or {})
    workflow["direction_proposal_request"] = {
        "status": "running", "job_id": job.id, "fingerprint": fingerprint,
        "version": version, "input_snapshot": parameters["input_snapshot"],
    }
    settings["creative_workflow"] = workflow
    item.settings = settings
    await append_message(
        session, item.id, "user", "direction_proposal_request",
        "按当前故事理解生成三个专业方向", job_id=job.id, parameters=parameters,
    )
    await session.flush()
    return job


async def propose_creative_directions(
    item: CreationSession,
    *,
    genre: str = "",
    conflict: str = "",
    characters: str = "",
    tone: str = "",
) -> dict[str, Any]:
    from app.services.story_direction_proposals import (
        propose_creative_directions as build_proposals,
        understanding_fingerprint,
    )

    return {
        "proposals": build_proposals(
            brief=item.brief,
            genre=genre,
            conflict=conflict,
            characters=characters,
            tone=tone,
        ),
        # 指纹跟着候选一起返回，前端才能判断“理解改了、候选过期”。
        "fingerprint": understanding_fingerprint(
            brief=item.brief,
            genre=genre,
            conflict=conflict,
            characters=characters,
            tone=tone,
        ),
    }


async def update_creative_specs(
    session: AsyncSession,
    item: CreationSession,
    project: Project,
    *,
    episode_count: int,
    episode_duration: int,
    market: str,
    narrative_spec: dict[str, Any] | None = None,
    expected_narrative_revision: int | None = None,
) -> CreationSession:
    patch = {
        "episode_count": episode_count,
        "episode_duration": episode_duration,
        "market": market,
    }
    project_settings = dict(project.creation_settings or {})
    previous_spec = narrative_spec_from_settings(project_settings)
    if narrative_spec is not None:
        project_settings = with_narrative_spec(
            {**project_settings, **patch},
            narrative_spec,
            expected_revision=expected_narrative_revision,
        )
    else:
        project_settings.update(patch)
        project_settings["narrative_spec"] = normalize_narrative_spec(
            project_settings.get("narrative_spec"),
            episode_count=episode_count,
            episode_duration=episode_duration,
        )
    project.creation_settings = project_settings
    session_settings = dict(item.settings or {})
    session_settings.update(patch)
    session_settings["narrative_spec"] = project_settings["narrative_spec"]
    impacts = narrative_spec_impact(
        previous_spec,
        project_settings["narrative_spec"],
    )
    if impacts:
        scope_result = await rebind_project_asset_scopes(
            session,
            project.id,
            session_settings,
            impacts,
        )
        session_settings = rebind_asset_breakdown_state(
            session_settings,
            scope_result,
            impacts,
        )
    item.settings = session_settings
    await session.flush()
    return item


async def confirm_creative_story(
    session: AsyncSession,
    item: CreationSession,
    action: str,
    adjustment: str,
) -> Job | None:
    settings = dict(item.settings)
    workflow = dict(settings.get("creative_workflow") or {})
    selected_option = str(workflow.get("selected_option") or "")
    direction = _resolve_creative_direction(selected_option)
    if action == "adjust":
        workflow.update({
            "stage": "awaiting_direction",
            "adjustment": adjustment.strip(),
        })
        settings["creative_workflow"] = workflow
        item.settings = settings
        await append_message(
            session,
            item.id,
            "user",
            "story_adjustment",
            adjustment.strip() or "返回调整创作方向",
        )
        await session.flush()
        return None

    workflow["stage"] = "generating_story_bible"
    settings["creative_workflow"] = workflow
    item.settings = settings
    market = "海外市场" if settings.get("market") == "overseas" else "国内市场"
    episode_count = int(settings.get("episode_count") or 10)
    episode_duration = int(settings.get("episode_duration") or 0)
    selected_proposal = workflow.get("selected_proposal")
    proposal_line = (
        f"已确认提案快照：{json.dumps(selected_proposal, ensure_ascii=False)}"
        if isinstance(selected_proposal, dict)
        else "未选择专业提案快照，按用户直接输入的方向执行。"
    )
    from app.services.episode_duration_policy import episode_duration_guidance
    duration_line = (episode_duration_guidance(episode_duration) if 1 <= episode_duration <= 3600
                     else "每集目标时长尚未确定，不要擅自写成固定秒数。")
    prompt = f"""请根据以下创作输入生成故事设定。只输出一个 JSON 对象，不要 Markdown，不要生成分集大纲。
JSON 必须完整包含 title、logline、genre、tone、audience、world、themes、characters、event_timeline。
characters 每项必须包含 name、role、goal、conflict、arc、importance、narrative_function、appearance_scope；event_timeline 每项必须包含 title、summary、episode_hint，episode_hint 只能是 1-300 的整数或 null。独立或混合结构时 event_timeline 表示逐集故事选题，禁止输出少量全季阶段节点。
{character_ecosystem_prompt(episode_count)}
创作方向：{direction['title']}。方向说明：{direction['description']}
{proposal_line}
目标市场：{market}。计划集数：{episode_count} 集。{duration_line}
不要预设喜剧或其他固定类型；题材、冲突和基调必须从原始创意与创作方向推导。
补充要求：{workflow.get('extra_requirements') or '无'}
用户原始创意：{item.brief}
请确保设定适合后续逐集生成，人物目标、冲突和世界规则明确。
{story_bible_strategy_prompt(item.settings)}"""
    return await create_story_bible_job(session, item, prompt_override=prompt)
