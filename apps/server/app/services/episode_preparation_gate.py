"""Episode-local confirmation and narrative-aware preparation prerequisites."""

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import Episode, Project, utcnow
from app.services.episode_generation_context import episode_generation_context
from app.services.script_finalization_service import require_episode_script_finalized


async def require_preparation(session, episode, *, confirming=False):
    project = await session.get(Project, episode.project_id)
    settings = project.creation_settings or {}
    if settings.get("outline_workflow", {}).get("status") == "pending":
        raise ConflictError("请先确认分集大纲修改")
    if episode.status == "archived" or not (episode.script or "").strip():
        raise ConflictError("本集已归档或正文为空")
    if not episode.duration_estimate or not 1 <= episode.duration_estimate <= 3600:
        raise ConflictError("请先设置本集目标时长（1–3600秒）")
    if episode.continuity_review_status in {"conflict", "needs_review"}:
        raise ConflictError(episode.continuity_review_reason or "请先处理本集一致性问题")
    if not confirming:
        require_episode_script_finalized(episode)
    context = episode_generation_context(settings.get("narrative_spec"), episode.number)
    if context["requires_previous"]:
        start = int((context.get("unit") or {}).get("episode_start") or 1)
        previous = list((await session.scalars(select(Episode).where(
            Episode.project_id == episode.project_id, Episode.number >= start,
            Episode.number < episode.number, Episode.status != "archived",
        ).order_by(Episode.number))).all())
        if [item.number for item in previous] != list(range(start, episode.number)):
            raise ConflictError("本集承接范围缺少前序分集")
        for item in previous:
            require_episode_script_finalized(item)
            if item.continuity_review_status in {"conflict", "needs_review"}:
                raise ConflictError(f"第{item.number}集承接资料尚需复核")


async def confirm_episode(session, episode, expected_revision):
    await require_preparation(session, episode, confirming=True)
    result = await session.execute(update(Episode).where(
        Episode.id == episode.id, Episode.script_revision == expected_revision,
    ).values(finalized_script_revision=expected_revision, script_finalized_at=utcnow())
      .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError("本集正文版本已变化，请刷新后重新确认")
    await session.refresh(episode)
    from app.services.story_continuity_service import sync_episode_continuity_records
    await sync_episode_continuity_records(session, episode, confirmation_status="confirmed")
    return episode
