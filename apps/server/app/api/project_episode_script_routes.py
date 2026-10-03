"""Episode script optimization, scene proposal, and CRUD routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.api.deps import CurrentUser, EpisodeDep, ProjectDep, SessionDep
from app.schemas.creation import (
    EpisodeScriptApplyRequest,
    EpisodeScriptOptimizeRequest,
    ScriptContinuityRepairApplyRequest,
    ScriptContinuityRepairRequest,
)
from app.schemas.job import JobOut
from app.schemas.project import (
    EpisodeOut,
    EpisodeSceneShotApplyOut,
    EpisodeSceneShotApplyRequest,
    EpisodeUpdate,
)
from app.services import creation_service, project_service

router = APIRouter(tags=["projects"])


class EpisodeConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_script_revision: int = Field(ge=1)
    confirmed: Literal[True]


@router.post("/{project_id}/episodes/{episode_id}/script-finalization", response_model=EpisodeOut)
async def confirm_episode_script(payload: EpisodeConfirmation, episode: EpisodeDep, session: SessionDep):
    from app.services.episode_preparation_gate import confirm_episode
    result = await confirm_episode(session, episode, payload.expected_script_revision)
    await session.commit()
    return EpisodeOut.model_validate(result)

@router.post(
    "/{project_id}/episodes/{episode_id}/optimize",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def optimize_episode_script(
    payload: EpisodeScriptOptimizeRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> JobOut:
    job = await creation_service.create_episode_script_optimization_job(
        session, episode, payload.instruction
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/continuity-repair",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_script_continuity_repair(
    payload: ScriptContinuityRepairRequest,
    project: ProjectDep,
    episode: EpisodeDep,
    session: SessionDep,
) -> JobOut:
    job = await creation_service.create_script_continuity_repair_job(
        session, project, episode, payload.issue_id, payload.instruction
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/continuity-repair/apply",
    response_model=EpisodeOut,
)
async def apply_script_continuity_repair(
    payload: ScriptContinuityRepairApplyRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeOut:
    updated = await creation_service.apply_script_continuity_repair(
        session,
        episode,
        payload.job_id,
        payload.expected_revision,
        user.id,
    )
    await session.commit()
    return EpisodeOut.model_validate(updated)


@router.post(
    "/{project_id}/episodes/{episode_id}/optimize/apply",
    response_model=EpisodeOut,
)
async def apply_episode_script_optimization(
    payload: EpisodeScriptApplyRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeOut:
    updated = await creation_service.apply_episode_script_optimization(
        session,
        episode,
        payload.job_id,
        payload.expected_revision,
        user.id,
    )
    await session.commit()
    return EpisodeOut.model_validate(updated)


@router.post(
    "/{project_id}/episodes/{episode_id}/scene-shot-proposal",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_episode_scene_shot_proposal(
    episode: EpisodeDep,
    session: SessionDep,
) -> JobOut:
    job = await creation_service.create_episode_scene_shot_proposal_job(
        session, episode
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/scene-shot-proposal/apply",
    response_model=EpisodeSceneShotApplyOut,
)
async def apply_episode_scene_shot_proposal(
    payload: EpisodeSceneShotApplyRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeSceneShotApplyOut:
    applied = await creation_service.apply_episode_scene_shot_proposal(
        session,
        episode,
        payload.job_id,
        payload.expected_script_revision,
        payload.content.model_dump(),
        user.id,
    )
    await session.commit()
    return EpisodeSceneShotApplyOut.model_validate(applied)


@router.patch("/{project_id}/episodes/{episode_id}", response_model=EpisodeOut)
async def update_episode(
    payload: EpisodeUpdate, episode: EpisodeDep, session: SessionDep, user: CurrentUser
) -> EpisodeOut:
    updated = await project_service.update_episode(
        session, episode, payload.model_dump(exclude_unset=True), actor_id=user.id
    )
    await session.commit()
    return EpisodeOut.model_validate(updated)


@router.delete(
    "/{project_id}/episodes/{episode_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_episode(episode: EpisodeDep, session: SessionDep) -> None:
    await project_service.delete_episode(session, episode)
    await session.commit()
