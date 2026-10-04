"""M3 Story Bible 业务逻辑：生成、确认与版本管理。

本模块处理故事设定的完整生命周期，从生成 Job 到确认写入正式项目。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_TYPE_STORY_BIBLE,
    CreationArtifact,
    CreationSession,
    Job,
    SESSION_STATUS_CONFIRMED,
    SESSION_STATUS_DRAFT,
    SESSION_STATUS_OUTLINE_CONFIRMED,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
)
from app.schemas.creation import StoryBibleContent
from app.services import project_service
from app.services.creation_session_service import (
    create_artifact,
    parse_structured_result,
)
from app.services.story_character_ecosystem import character_ecosystem_prompt, enrich_story_bible
from app.services.narrative_prompt_service import story_bible_strategy_prompt
from app.services.narrative_spec_service import confirm_explicit_narrative_spec
from app.services.story_bible_structure_review import (
    review_story_bible_structure,
    validate_story_bible_structure,
)


def _episode_count(item: CreationSession) -> int:
    value = item.settings.get("episode_count", 10)
    from app.core.creation_limits import episode_count
    return episode_count(value)


async def create_story_bible_job(
    session: AsyncSession,
    item: CreationSession,
    *,
    prompt_override: str | None = None,
) -> Job:
    from sqlalchemy import select
    from app.models import JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING, SESSION_STATUS_GENERATING
    from app.services import job_service
    from app.services.creation_agent_service import _resolve_agent_execution, _attach_agent_execution
    await session.flush()
    await session.refresh(item, with_for_update=True)
    
    active = await session.scalar(select(Job.id).where(
        Job.owner_id == item.owner_id,
        Job.target_type == ARTIFACT_TYPE_STORY_BIBLE,
        Job.target_id == item.id,
        Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING, "downloading"]),
    ))
    if active is not None:
        raise ConflictError("故事设定正在生成，请等待当前任务完成")
    from app.services.long_form_workflow import resume_existing
    resumed = await resume_existing(session, item, "story")
    if resumed is not None:
        return resumed
    if item.status not in {SESSION_STATUS_DRAFT, SESSION_STATUS_REVIEWING}:
        latest_story = await session.scalar(select(CreationArtifact).where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
            CreationArtifact.status != "superseded",
        ).order_by(CreationArtifact.version.desc()).limit(1))
        repair_allowed = (
            item.status == SESSION_STATUS_CONFIRMED
            and latest_story is not None
            and review_story_bible_structure(latest_story.content, item.settings)["error_count"] > 0
        )
        if not repair_allowed:
            raise ConflictError("故事设定已确认，不能重新生成")
    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "outline", "story_bible"
    )
    from app.services.source_index_service import resolve_source_text
    source_text = await resolve_source_text(session, item)
    prompt = f"{instruction_prefix}\n\n{prompt_override or build_story_bible_prompt(item, source_text)}"
    job = await job_service.create_text_job(
        session,
        item.owner_id,
        provider_model_id=model.id,
        prompt=prompt,
        project_id=None,
        parameters={},
    )
    job.target_type = ARTIFACT_TYPE_STORY_BIBLE
    job.target_id = item.id
    _attach_agent_execution(job, execution)
    from app.services.long_form_workflow import BATCH, configure
    if int(item.settings.get("episode_count") or 10) > BATCH or len(source_text) > 6000:
        await configure(session, item, job, "story")
    item.status = SESSION_STATUS_GENERATING
    await session.flush()
    return job


def build_story_bible_prompt(item: CreationSession, source_text: str | None = None) -> str:
    from app.services.creation_session_service import chunk_reference_text
    settings = dict(item.settings)
    reference_text = str(settings.pop("reference_text", "") or item.brief)
    if source_text is not None:
        reference_text = source_text
    chunks = chunk_reference_text(reference_text)
    source_context = chunks[0]["content"] if chunks else item.brief
    import json
    return f"""你是短剧策划。根据用户灵感生成故事设定，只输出一个 JSON 对象，不要 Markdown。
JSON 字段必须为：title, logline, genre, tone, audience, world, themes, characters, event_timeline。
title、logline、genre、tone、audience、world 必须是字符串；themes 必须是字符串数组。
characters 是数组，每项字段必须为：name, role, goal, conflict, arc, importance, narrative_function, appearance_scope。
可选角色资料：character_id、aliases、age、description、personality、appearance、costume、voice。保留已有身份编号与原文；未知年龄、外貌、服装或声线可留空，不伪造精确资料。voice描述音高、质感、语速、口音和表达特点，不绑定真实音色。
event_timeline 是按已选剧集结构生成的事件规划数组，每项字段为：title, summary, episode_hint；episode_hint 只能是 1 到 300 的整数，事件说明只写在 summary。独立或混合结构时它代表逐集故事选题，不是全季阶段节点。
{character_ecosystem_prompt(_episode_count(item))}
标题：{item.title}
用户灵感：{item.brief}
当前参考片段（1/{max(1, len(chunks))}）：{source_context}
创作设置：{json.dumps(settings, ensure_ascii=False)}
{story_bible_strategy_prompt(item.settings)}"""


async def update_story_bible(
    session: AsyncSession,
    item: CreationSession,
    artifact_id: int,
    content: dict[str, Any],
) -> CreationArtifact:
    artifact = await session.get(CreationArtifact, artifact_id)
    if (
        artifact is None
        or artifact.session_id != item.id
        or artifact.status != ARTIFACT_STATUS_DRAFT
    ):
        raise NotFoundError("可编辑的故事设定不存在")
    validated = StoryBibleContent.model_validate(content).model_dump()
    validated = enrich_story_bible(validated, _episode_count(item))
    artifact.content = validated
    artifact.revision += 1
    await session.flush()
    return artifact


async def confirm_story_bible(
    session: AsyncSession, item: CreationSession, artifact_id: int, expected_revision: int
) -> CreationArtifact:
    artifact = await session.get(CreationArtifact, artifact_id)
    latest = await session.scalar(select(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
        CreationArtifact.status != "superseded",
    ).order_by(CreationArtifact.version.desc()).limit(1))
    if artifact is None or artifact.session_id != item.id or artifact.artifact_type != ARTIFACT_TYPE_STORY_BIBLE or artifact.status != ARTIFACT_STATUS_DRAFT:
        raise NotFoundError("可确认的故事设定不存在")
    if latest is None or latest.id != artifact.id:
        raise ConflictError("故事设定已有更新版本，请刷新后确认最新草稿")
    if artifact.revision != expected_revision:
        raise ConflictError("故事设定已被其他窗口修改，请刷新后重新审核")
    validate_story_bible_structure(artifact.content, item.settings)
    item.settings = confirm_explicit_narrative_spec(item.settings)
    from sqlalchemy import update
    locked = await session.execute(update(CreationArtifact).where(
        CreationArtifact.id == artifact.id,
        CreationArtifact.revision == expected_revision,
        CreationArtifact.status == ARTIFACT_STATUS_DRAFT,
    ).values(status=ARTIFACT_STATUS_CONFIRMED, revision=expected_revision + 1).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise ConflictError("故事设定已变化，请刷新后确认最新版本")
    await session.refresh(artifact)
    await session.execute(update(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
        CreationArtifact.id != artifact.id,
        CreationArtifact.status != "superseded",
    ).values(status="superseded"))
    downstream = await session.scalar(select(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == "episode_outline",
        CreationArtifact.status != "superseded",
    ).order_by(CreationArtifact.version.desc()).limit(1))
    if item.status in {SESSION_STATUS_DRAFT, SESSION_STATUS_REVIEWING, SESSION_STATUS_CONFIRMED}:
        if downstream is None:
            item.status = SESSION_STATUS_CONFIRMED
        else:
            item.status = (
                SESSION_STATUS_OUTLINE_CONFIRMED
                if downstream.status == ARTIFACT_STATUS_CONFIRMED
                else SESSION_STATUS_OUTLINE_REVIEWING
            )
    settings = dict(item.settings or {})
    settings["story_workflow"] = {
        "confirmed_artifact_id": artifact.id,
        "confirmed_version": artifact.version,
        "confirmed_revision": artifact.revision,
        "downstream_review_required": downstream is not None,
        "downstream_outline_id": downstream.id if downstream is not None else None,
        "downstream_outline_version": downstream.version if downstream is not None else None,
    }
    item.settings = settings
    if item.project_id is not None:
        project = await project_service.get_project(session, item.project_id, item.owner_id)
        project_settings = dict(project.creation_settings or {})
        project_settings["narrative_spec"] = item.settings.get("narrative_spec")
        project.creation_settings = project_settings
        content = StoryBibleContent.model_validate(artifact.content)
        await project_service.update_project(session, project, {
            "name": content.title,
            "description": content.logline,
            "genre": content.genre[:64],
        })
    await session.flush()
    return artifact


async def save_story_bible_version(session: AsyncSession, item: CreationSession, artifact_id: int, content: dict[str, Any], expected_revision: int) -> CreationArtifact:
    """R2 manual edit: keep confirmed history, reject stale edits, never rewrite outlines/scripts."""
    from sqlalchemy import update
    current = await session.scalar(select(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
        CreationArtifact.status != "superseded",
    ).order_by(CreationArtifact.version.desc()).limit(1))
    if current is None or current.id != artifact_id or current.revision != expected_revision:
        raise ConflictError("故事设定已有新版本，请刷新后合并修改；当前编辑内容未保存")
    validated = StoryBibleContent.model_validate(content).model_dump()
    validated = enrich_story_bible(validated, _episode_count(item))
    identities = [person.get("character_id") for person in validated["characters"] if person.get("character_id")]
    if len(identities) != len(set(identities)):
        raise ConflictError("人物身份编号重复，请重新加载角色资料")
    locked = await session.execute(update(CreationArtifact).where(
        CreationArtifact.id == current.id, CreationArtifact.revision == expected_revision,
    ).values(revision=expected_revision + 1).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise ConflictError("故事设定正在被其他请求修改，请刷新后重试")
    result = await create_artifact(session, item, None, ARTIFACT_TYPE_STORY_BIBLE,
        {**current.content, **validated}, "故事策划局部修改已保存为新草稿，原大纲与正文保持不变。")
    return result


def parse_story_bible(text: str) -> dict[str, Any]:
    return parse_structured_result(text, StoryBibleContent, "故事设定")


async def finalize_story_bible_job(
    session: AsyncSession,
    item: CreationSession,
    job: Job,
    result: dict[str, Any],
) -> None:
    """Worker 完成 Story Bible 生成后的落库逻辑。"""
    content = enrich_story_bible(parse_story_bible(str(result.get("text", ""))), _episode_count(item))
    result["story_bible_structure_review"] = review_story_bible_structure(content, item.settings)
    await create_artifact(session, item, job, ARTIFACT_TYPE_STORY_BIBLE, content, "故事设定已生成，请查看并确认。")
    item.status = SESSION_STATUS_REVIEWING
    await session.flush()


from sqlalchemy import select
