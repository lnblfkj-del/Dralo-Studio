# ruff: noqa: RUF001
"""One-time recovery of pre-project CreationSession rows into the unified workspace."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    ARTIFACT_STATUS_SUPERSEDED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_STORY_BIBLE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
    SESSION_STATUS_SCRIPT_REVIEWING,
    CreationArtifact,
    CreationSession,
    Job,
    Project,
    User,
    utcnow,
)
from app.schemas.creation import EpisodeOutlineContent, EpisodeScriptContent, StoryBibleContent
from app.services import project_service, script_version_service
from app.services.creation_session_service import CREATION_ARTIFACT_TYPES, append_message


async def _latest_artifacts(
    session: AsyncSession, item: CreationSession
) -> dict[str, CreationArtifact]:
    artifacts = list(
        (
            await session.scalars(
                select(CreationArtifact)
                .where(
                    CreationArtifact.session_id == item.id,
                    CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
                )
                .order_by(CreationArtifact.version.desc(), CreationArtifact.id.desc())
            )
        ).all()
    )
    return {artifact.artifact_type: artifact for artifact in artifacts}


def _validated(content: dict, schema):
    try:
        return schema.model_validate(content)
    except ValidationError:
        return None


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


async def recover_legacy_creation_session(
    session: AsyncSession, item: CreationSession, owner: User
) -> Project:
    """Bind one legacy session to one project without inventing upstream artifacts."""

    if item.project_id is not None:
        return await project_service.get_project(session, item.project_id, owner.id)
    active_job = await session.scalar(
        select(Job.id)
        .where(
            Job.owner_id == item.owner_id,
            Job.target_id == item.id,
            Job.target_type.in_(CREATION_ARTIFACT_TYPES),
            Job.status.in_(
                (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_RETRYING)
            ),
        )
        .limit(1)
    )
    if active_job is not None:
        raise ConflictError("旧创作会话仍有任务运行，请等待任务结束后再恢复")

    artifacts = await _latest_artifacts(session, item)
    story_artifact = artifacts.get(ARTIFACT_TYPE_STORY_BIBLE)
    outline_artifact = artifacts.get(ARTIFACT_TYPE_EPISODE_OUTLINE)
    script_artifact = artifacts.get(ARTIFACT_TYPE_EPISODE_SCRIPT)
    story = _validated(story_artifact.content, StoryBibleContent) if story_artifact else None
    outline = (
        _validated(outline_artifact.content, EpisodeOutlineContent) if outline_artifact else None
    )
    script = _validated(script_artifact.content, EpisodeScriptContent) if script_artifact else None

    settings = dict(item.settings or {})
    settings.setdefault("source_type", "idea")
    settings.setdefault("brief", item.brief)
    settings["legacy_recovery"] = {
        "session_id": item.id,
        "recovered_at": utcnow().isoformat(),
        "story_artifact_id": story_artifact.id if story_artifact else None,
        "outline_artifact_id": outline_artifact.id if outline_artifact else None,
        "script_artifact_id": script_artifact.id if script_artifact else None,
        "upstream_artifacts_invented": False,
    }
    if settings["source_type"] == "upload":
        analysis = dict(settings.get("import_analysis") or {})
        analysis.setdefault("material_type", "full_script" if script else "story_outline")
        analysis.setdefault("source_preserved", bool(settings.get("reference_text") or item.brief))
        settings["import_analysis"] = analysis

    project = await project_service.create_project(
        session,
        owner,
        {
            "name": (story.title if story else item.title).strip()[:255]
            or f"恢复的创作会话 {item.id}",
            "description": (story.logline if story else item.brief).strip()[:4000],
            "genre": story.genre[:64] if story else None,
            "creation_settings": settings,
        },
    )
    planned = list(outline.episodes) if outline else []
    configured = _bounded_int(settings.get("episode_count"), default=1, minimum=1, maximum=100)
    episode_count = min(
        max(len(planned), script.episode_number if script else 0, configured),
        100,
    )
    default_duration = _bounded_int(
        settings.get("episode_duration"), default=90, minimum=1, maximum=3600
    )

    episodes = {}
    for number in range(1, episode_count + 1):
        plan = planned[number - 1] if number <= len(planned) else None
        episode = await project_service.create_episode(
            session,
            project,
            {
                "number": number,
                "title": (script.title if script and script.episode_number == number else None)
                or (plan.title if plan else f"第 {number} 集"),
                "synopsis": (
                    script.synopsis if script and script.episode_number == number else None
                )
                or (plan.synopsis if plan else None),
                "duration_estimate": (plan.duration_seconds if plan else None) or default_duration,
            },
        )
        episodes[number] = episode
    if script is not None:
        episode = episodes[script.episode_number]
        await script_version_service.save_script(
            session,
            episode,
            script.script,
            expected=episode.script_revision,
            actor_id=owner.id,
            note=f"从旧创作会话 #{item.id} 恢复",
            source="ai",
        )

    item.project_id = project.id
    item.settings = settings
    if script is not None:
        item.status = SESSION_STATUS_SCRIPT_REVIEWING
    elif outline is not None:
        item.status = SESSION_STATUS_OUTLINE_REVIEWING
    elif story is not None:
        item.status = SESSION_STATUS_REVIEWING
    await append_message(
        session,
        item.id,
        "system",
        "legacy_recovery",
        f"旧创作会话已恢复到统一项目 #{project.id}；原始消息和产物保持不变。",
        parameters={"project_id": project.id, "upstream_artifacts_invented": False},
    )
    await session.flush()
    return project
