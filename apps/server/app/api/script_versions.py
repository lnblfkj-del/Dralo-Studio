from fastapi import APIRouter

from app.api.deps import CurrentUser, EpisodeDep, PageDep, SessionDep
from app.schemas.common import Page
from app.schemas.project import EpisodeOut
from app.schemas.script_version import RestoreScript, ScriptVersionOut, ScriptVersionSummary
from app.services import script_version_service as service

router = APIRouter(prefix="/projects/{project_id}/episodes/{episode_id}/versions", tags=["script versions"])


@router.get("", response_model=Page[ScriptVersionSummary])
async def versions(episode: EpisodeDep, session: SessionDep, page: PageDep):
    items, total = await service.list_versions(session, episode.id, limit=page.page_size, offset=page.offset)
    return Page[ScriptVersionSummary](items=[ScriptVersionSummary.model_validate(item) for item in items],
                                       total=total, page=page.page, page_size=page.page_size)


@router.get("/{version_id}", response_model=ScriptVersionOut)
async def version(version_id: int, episode: EpisodeDep, session: SessionDep):
    return await service.get_version(session, episode.id, version_id)


@router.post("/{version_id}/restore", response_model=EpisodeOut)
async def restore(version_id: int, payload: RestoreScript, episode: EpisodeDep,
                  session: SessionDep, user: CurrentUser):
    selected = await service.get_version(session, episode.id, version_id)
    result = await service.save_script(session, episode, selected.script, expected=payload.expected_revision,
                                       actor_id=user.id, restored_from=selected.revision,
                                       note=f"恢复自 V{selected.revision}")
    await session.commit()
    return EpisodeOut.model_validate(result)
