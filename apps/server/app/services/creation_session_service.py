"""M3 创作会话基础：CreationSession 的 CRUD 与消息/产物管理。

本模块只负责会话本身的生命周期，不涉及 Story Bible、大纲、脚本等具体业务。
"""

import re
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, undefer

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_STATUS_SUPERSEDED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_SCENE_SHOT_DRAFT,
    ARTIFACT_TYPE_STORY_BIBLE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_DOWNLOADING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_TARGET_OUTLINE_AGENT,
    SESSION_STATUS_BREAKDOWN_COMPLETED,
    SESSION_STATUS_BREAKDOWN_GENERATING,
    SESSION_STATUS_BREAKDOWN_REVIEWING,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_CONFIRMED,
    SESSION_STATUS_DRAFT,
    SESSION_STATUS_GENERATING,
    SESSION_STATUS_OUTLINE_CONFIRMED,
    SESSION_STATUS_OUTLINE_GENERATING,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
    SESSION_STATUS_SCRIPT_GENERATING,
    SESSION_STATUS_SCRIPT_REVIEWING,
    CreationArtifact,
    CreationMessage,
    CreationSession,
    Episode,
    Job,
    Project,
    User,
)
from app.schemas.creation import (
    EpisodeOutlineContent,
    EpisodeScriptContent,
    EpisodeScriptOptimizationResult,
    OutlineAgentResult,
    SceneShotDraftContent,
    StoryBibleContent,
    UploadedScriptOptimizationResult,
)
from app.services import job_service
from app.services.project_source_service import synchronize_source_fields
from app.services.structured_output_service import parse_structured_result
from app.services.team_access import owner_scope

JOB_TARGET_SCRIPT_IMPORT = "script_import_optimization"
JOB_TARGET_CREATIVE_DIRECTION = "creative_direction"
JOB_TARGET_SCRIPT_STUDY_BATCH = "script_study_batch"
JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP = "script_study_batch_group"
JOB_TARGET_SCRIPT_STORY_EXTRACTION = "script_story_extraction"
JOB_TARGET_SCRIPT_ASSET_BREAKDOWN = "script_asset_breakdown"
JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH = "script_asset_breakdown_batch"
JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP = "script_asset_breakdown_group"
JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION = "episode_script_optimization"
JOB_TARGET_EPISODE_SCRIPT_GENERATION = "episode_script_generation"
JOB_TARGET_EPISODE_SCRIPT_BATCH = "episode_script_batch"
JOB_TARGET_SCRIPT_CONTINUITY_CHECK = "script_continuity_check"
JOB_TARGET_SCRIPT_CONTINUITY_REPAIR = "script_continuity_repair"
JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL = "episode_scene_shot_proposal"
SCRIPT_STUDY_BATCH_SIZE = 5

CREATION_ARTIFACT_TYPES = {
    "outline_continuation",
    ARTIFACT_TYPE_STORY_BIBLE,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_SCENE_SHOT_DRAFT,
    JOB_TARGET_OUTLINE_AGENT,
    JOB_TARGET_CREATIVE_DIRECTION,
    JOB_TARGET_SCRIPT_IMPORT,
    JOB_TARGET_SCRIPT_STUDY_BATCH,
    JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP,
    JOB_TARGET_SCRIPT_STORY_EXTRACTION,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_BATCH,
    JOB_TARGET_SCRIPT_CONTINUITY_CHECK,
    JOB_TARGET_SCRIPT_CONTINUITY_REPAIR,
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
}

REFERENCE_CHUNK_CHARS = 6000


async def _backfill_uploaded_outline(
    session: AsyncSession, item: CreationSession, project: Project
) -> None:
    """Bring pre-M5 story-outline imports onto the unified artifact contract."""
    settings = item.settings or {}
    analysis = settings.get("import_analysis")
    if not isinstance(analysis, dict) or analysis.get("material_type") != "story_outline":
        return
    exists = await session.scalar(select(CreationArtifact.id).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE,
    ).limit(1))
    if exists is not None:
        return
    episodes = list((await session.execute(
        select(Episode).where(Episode.project_id == project.id, Episode.status != "archived").order_by(Episode.number)
    )).scalars())
    if not episodes:
        return
    session.add(CreationArtifact(
        session_id=item.id,
        artifact_type=ARTIFACT_TYPE_EPISODE_OUTLINE,
        version=1,
        revision=0,
        status=ARTIFACT_STATUS_DRAFT,
        content={"episodes": [{
            "number": episode.number,
            "title": episode.title or f"第 {episode.number} 集",
            "synopsis": episode.synopsis or "待补充本集梗概",
            "dramatic_goal": "推进本集已上传大纲中的核心事件",
            "cliffhanger": "",
            "duration_seconds": episode.duration_estimate,
        } for episode in episodes]},
        source_job_id=None,
    ))
    later_statuses = {
        SESSION_STATUS_SCRIPT_GENERATING,
        SESSION_STATUS_SCRIPT_REVIEWING,
        SESSION_STATUS_COMPLETED,
        SESSION_STATUS_BREAKDOWN_GENERATING,
        SESSION_STATUS_BREAKDOWN_REVIEWING,
        SESSION_STATUS_BREAKDOWN_COMPLETED,
    }
    if item.status not in later_statuses:
        item.status = SESSION_STATUS_OUTLINE_REVIEWING
    item.settings = {
        **settings,
        "outline_import": {
            "source": "legacy_project_backfill",
            "import_session_id": settings.get("import_session_id"),
            "source_sha256": analysis.get("source_sha256"),
            "source_preserved": True,
            "story_bible_optional": True,
        },
    }
    await session.flush()


async def create_session(
    session: AsyncSession,
    owner_id: int,
    *,
    title: str,
    brief: str,
    settings: dict[str, Any],
    project_id: int | None = None,
) -> CreationSession:
    settings = dict(settings)
    if project_id is not None and settings.get("reference_text") and not settings.get("import_session_id"):
        settings["reference_project_id"] = project_id
        settings["reference_text"] = ""
    item = CreationSession(
        owner_id=owner_id,
        project_id=project_id,
        title=title.strip(),
        brief=brief.strip(),
        settings=settings,
        status=SESSION_STATUS_DRAFT,
    )
    session.add(item)
    await session.flush()
    session.add(CreationMessage(
        session_id=item.id, role="user", message_type="brief", content=item.brief, sequence=1
    ))
    await session.flush()
    return item


async def get_session(
    session: AsyncSession, session_id: int, owner_id: int
) -> CreationSession:
    item = await session.scalar(select(CreationSession).where(
        CreationSession.id == session_id, owner_scope(CreationSession.owner_id, owner_id)
    ))
    if item is None:
        raise NotFoundError("创作会话不存在")
    return item


async def get_or_create_project_session(
    session: AsyncSession, project: Project
) -> CreationSession:
    item = await session.scalar(
        select(CreationSession)
        .where(
            CreationSession.project_id == project.id,
            CreationSession.owner_id == project.owner_id,
        )
        .order_by(CreationSession.id.desc())
        .limit(1)
    )
    if item is not None:
        synchronized = synchronize_source_fields(item.settings, project.creation_settings)
        if synchronized != item.settings:
            item.settings = synchronized
            await session.flush()
        await _backfill_uploaded_outline(session, item, project)
        return item
    settings = dict(project.creation_settings or {})
    brief = str(settings.get("brief") or project.description or "从空白项目开始创作。")
    item = await create_session(
        session,
        project.owner_id,
        title=project.name,
        brief=brief,
        settings=settings,
        project_id=project.id,
    )
    await _backfill_uploaded_outline(session, item, project)
    return item


async def to_session_out(session: AsyncSession, item: CreationSession, *, compact: bool = False) -> dict[str, Any]:
    messages = list((await session.execute(
        select(CreationMessage).where(CreationMessage.session_id == item.id)
        .order_by(CreationMessage.sequence)
    )).scalars())
    artifacts = list((await session.execute(
        select(CreationArtifact).where(CreationArtifact.session_id == item.id, CreationArtifact.artifact_type.not_in(["outline_continuation", "long_form_work"]))
        .options(defer(CreationArtifact.content) if compact else undefer(CreationArtifact.content))
        .order_by(CreationArtifact.version.desc())
    )).scalars())
    active_job = await session.scalar(
        select(Job).where(
            Job.deleted_at.is_(None),
            Job.owner_id == item.owner_id,
            Job.target_type.in_(CREATION_ARTIFACT_TYPES),
            Job.target_id == item.id,
            Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_DOWNLOADING, JOB_STATUS_RETRYING]),
        ).order_by(Job.id.desc()).limit(1)
    )
    latest_job = await session.scalar(
        select(Job).where(
            Job.deleted_at.is_(None),
            Job.owner_id == item.owner_id,
            Job.target_type.in_(CREATION_ARTIFACT_TYPES),
            Job.target_id == item.id,
        ).order_by(Job.id.desc()).limit(1)
    )
    from app.services.story_character_ecosystem import enrich_story_bible

    from app.core.creation_limits import episode_count as validate_episode_count
    episode_count = validate_episode_count(item.settings.get("episode_count"))
    def lazy_history(artifact):
        return compact and artifact.status == ARTIFACT_STATUS_SUPERSEDED and artifact.artifact_type in (ARTIFACT_TYPE_STORY_BIBLE, ARTIFACT_TYPE_EPISODE_OUTLINE)

    contents = {}
    if compact:
        ids = [artifact.id for artifact in artifacts if not lazy_history(artifact)]
        if ids:
            contents = dict((await session.execute(select(CreationArtifact.id, CreationArtifact.content).where(CreationArtifact.id.in_(ids)))).all())
    output_artifacts = []
    for artifact in artifacts:
        if compact:
            content = contents.get(artifact.id, {})
            if artifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE and not lazy_history(artifact):
                content = enrich_story_bible(content, episode_count, assign_ids=False)
            output_artifacts.append({
                "id": artifact.id, "artifact_type": artifact.artifact_type,
                "version": artifact.version, "revision": artifact.revision, "status": artifact.status,
                "content": content, "content_loaded": not lazy_history(artifact), "source_job_id": artifact.source_job_id,
                "created_at": artifact.created_at, "updated_at": artifact.updated_at,
            })
            continue
        if artifact.artifact_type != ARTIFACT_TYPE_STORY_BIBLE:
            output_artifacts.append(artifact)
            continue
        output_artifacts.append({
            "id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "version": artifact.version,
            "revision": artifact.revision,
            "status": artifact.status,
            "content": enrich_story_bible(artifact.content, episode_count, assign_ids=False),
            "source_job_id": artifact.source_job_id,
            "created_at": artifact.created_at,
            "updated_at": artifact.updated_at,
        })
    from app.services.long_form_progress import progress
    return {
        "workflow_progress": await progress(session, item),
        "id": item.id,
        "owner_id": item.owner_id,
        "project_id": item.project_id,
        "title": item.title,
        "brief": item.brief,
        "settings": item.settings,
        "status": item.status,
        "active_job_id": active_job.id if active_job is not None else None,
        "active_job_target": active_job.target_type if active_job is not None else None,
        "active_job_status": active_job.status if active_job is not None else None,
        "active_job_progress": active_job.progress if active_job is not None else None,
        "latest_job_id": latest_job.id if latest_job is not None else None,
        "latest_job_target": latest_job.target_type if latest_job is not None else None,
        "latest_job_status": latest_job.status if latest_job is not None else None,
        "latest_job_error": latest_job.error_message if latest_job is not None else None,
        "messages": messages,
        "artifacts": output_artifacts,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


async def create_artifact(
    session: AsyncSession,
    item: CreationSession,
    job: Job | None,
    artifact_type: str,
    content: dict[str, Any],
    message: str,
) -> CreationArtifact:
    # 先执行原子 UPDATE 把旧 draft 置为 superseded。这一步会触发写事务、
    # 获取写锁（SQLite WAL 下为 RESERVED 锁），使并发写入在此串行化，
    # 从而保证下面 max(version) 读取到的是一致的最新值，避免版本号重复。
    await session.execute(
        update(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == artifact_type,
            CreationArtifact.status == ARTIFACT_STATUS_DRAFT,
        )
        .values(status=ARTIFACT_STATUS_SUPERSEDED)
    )
    version = int(await session.scalar(select(func.coalesce(func.max(CreationArtifact.version), 0)).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == artifact_type,
    ))) + 1
    if artifact_type == ARTIFACT_TYPE_STORY_BIBLE:
        from app.services.story_character_ecosystem import enrich_story_bible
        from app.core.creation_limits import episode_count
        content = enrich_story_bible(content, episode_count(item.settings.get("episode_count")))
    artifact = CreationArtifact(
        session_id=item.id,
        artifact_type=artifact_type,
        version=version,
        status=ARTIFACT_STATUS_DRAFT,
        content=content,
        source_job_id=job.id if job is not None else None,
    )
    session.add(artifact)
    await append_message(session, item.id, "assistant", artifact_type, message)
    await session.flush()
    return artifact


async def append_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    message_type: str,
    content: str,
    *,
    job_id: int | None = None,
    parameters: dict[str, Any] | None = None,
) -> CreationMessage:
    sequence = int(await session.scalar(select(func.coalesce(func.max(CreationMessage.sequence), 0)).where(
        CreationMessage.session_id == session_id
    ))) + 1
    message = CreationMessage(
        session_id=session_id,
        role=role,
        message_type=message_type,
        content=content,
        sequence=sequence,
        job_id=job_id,
        parameters=parameters or {},
    )
    session.add(message)
    await session.flush()
    return message


async def get_artifact(
    session: AsyncSession,
    item: CreationSession,
    artifact_type: str,
    *,
    status: str,
) -> CreationArtifact:
    artifact = await session.scalar(
        select(CreationArtifact).where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == artifact_type,
            CreationArtifact.status == status,
        ).order_by(CreationArtifact.version.desc()).limit(1)
    )
    if artifact is None:
        raise ConflictError("创作阶段数据不完整，请返回上一步检查")
    return artifact


def chunk_reference_text(text: str, max_chars: int = REFERENCE_CHUNK_CHARS) -> list[dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    sections = re.split(
        r"(?=^(?:#{1,3}\s+.+|第.{1,20}[章节集幕](?:\s|$).*))",
        normalized,
        flags=re.MULTILINE,
    )
    chunks: list[dict[str, Any]] = []
    for section in (part.strip() for part in sections if part.strip()):
        for offset in range(0, len(section), max_chars):
            content = section[offset:offset + max_chars]
            first_line = content.splitlines()[0].lstrip("# ").strip()
            chunks.append({
                "id": len(chunks),
                "title": first_line[:80] or f"片段 {len(chunks) + 1}",
                "char_count": len(content),
                "preview": content[:160],
                "content": content,
            })
    return chunks


def list_reference_chunks(item: CreationSession, source_text: str | None = None) -> list[dict[str, Any]]:
    source = source_text if source_text is not None else str(item.settings.get("reference_text") or item.brief)
    return [
        {key: value for key, value in chunk.items() if key != "content"}
        for chunk in chunk_reference_text(source)
    ]

