"""Keep the legacy E5 exporters from silently discarding saved T1 edits."""

# ruff: noqa: RUF001 -- Chinese user-facing punctuation is intentional.

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.edit_project import EditProject
from app.models.episode_edit import EpisodeEditDraft

EDIT_DRAFT_EXPORT_MESSAGE = (
    "本集有已保存的多轨剪辑草稿，旧整集合成尚不能应用这些修改；请勿按旧时间线导出"
)


async def pending_edit_draft(session: AsyncSession, episode_id: int) -> dict[str, object] | None:
    """An unmigrated database cannot contain a persisted T1 edit."""
    has_table = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(
            EpisodeEditDraft.__tablename__
        )
    )
    independent_table = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(EditProject.__tablename__)
    )
    if independent_table:
        independent = (
            await session.execute(
                select(EditProject.id, EditProject.revision, EditProject.fingerprint)
                .where(EditProject.source_episode_id == episode_id, EditProject.revision > 0)
                .order_by(EditProject.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if independent is not None:
            return {
                "edit_project_id": independent.id,
                "revision": independent.revision,
                "fingerprint": independent.fingerprint,
            }
    if not has_table:
        return None
    row = (
        await session.execute(
            select(EpisodeEditDraft.revision, EpisodeEditDraft.fingerprint).where(
                EpisodeEditDraft.episode_id == episode_id
            )
        )
    ).one_or_none()
    if row is None or row.revision == 0:
        return None
    return {"revision": row.revision, "fingerprint": row.fingerprint}


async def assert_legacy_export_allowed(session: AsyncSession, episode_id: int) -> None:
    if await pending_edit_draft(session, episode_id) is not None:
        raise ConflictError(EDIT_DRAFT_EXPORT_MESSAGE)
