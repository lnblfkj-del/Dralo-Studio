from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Episode, ScriptVersion


async def save_script(session: AsyncSession, episode: Episode, script: str | None, *, expected: int,
                      actor_id: int | None, note: str | None = None, restored_from: int | None = None,
                      source: str = "manual") -> Episode:
    old_script = episode.script or ""
    script = script or ""
    if episode.script_revision != expected:
        raise ConflictError("剧本已在其他页面更新，请先查看最新内容；你的输入尚未覆盖服务器")
    changed = old_script != script or restored_from is not None
    next_revision = expected + 1 if changed else expected
    values = {"script": script, "script_revision": next_revision}
    result = await session.execute(update(Episode).where(
        Episode.id == episode.id, Episode.script_revision == expected,
    ).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError("剧本已在其他页面更新，请先查看最新内容；你的输入尚未覆盖服务器")
    if changed:
        previous = await session.scalar(select(ScriptVersion.id).where(
            ScriptVersion.episode_id == episode.id, ScriptVersion.revision == expected,
        ))
        if previous is None:
            session.add(ScriptVersion(episode_id=episode.id, revision=expected, script=old_script,
                                      character_count=len(old_script), source="initial", created_by=None))
        session.add(ScriptVersion(episode_id=episode.id, revision=next_revision, script=script,
                      character_count=len(script), source="restore" if restored_from is not None else source,
                                  created_by=actor_id, note=note, restored_from=restored_from))
    await session.flush()
    await session.refresh(episode)
    if changed:
        from app.services.script_finalization_service import invalidate_script_dependents

        await invalidate_script_dependents(session, episode, expected)
        from app.services.screenplay_preflight import episode_review
        review = await episode_review(session, episode)
        if review["blocking"]:
            episode.continuity_review_status = "warning"
            episode.continuity_review_reason = "正文已保存，确认前请核对：" + review["errors"][0]["message"]
    return episode


async def list_versions(session: AsyncSession, episode_id: int, *, limit: int, offset: int):
    # List only metadata; large manuscripts are fetched one at a time.
    columns = [ScriptVersion.id, ScriptVersion.episode_id, ScriptVersion.revision,
               ScriptVersion.character_count, ScriptVersion.source, ScriptVersion.note,
               ScriptVersion.restored_from, ScriptVersion.created_by, ScriptVersion.created_at]
    rows = (await session.execute(select(*columns).where(ScriptVersion.episode_id == episode_id)
                                  .order_by(ScriptVersion.revision.desc()).limit(limit).offset(offset))).mappings().all()
    total = await session.scalar(select(func.count()).select_from(ScriptVersion).where(ScriptVersion.episode_id == episode_id))
    return rows, total


async def get_version(session: AsyncSession, episode_id: int, version_id: int) -> ScriptVersion:
    version = await session.get(ScriptVersion, version_id)
    if version is None or version.episode_id != episode_id:
        raise NotFoundError("剧本版本不存在")
    return version
