"""Scoped, provider-free recovery inspection and original-job resume."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.services import segment_video_recovery_service as service

router = APIRouter(tags=["projects"])
PREFIX = "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/video-recovery"


class ResumeRequest(BaseModel):
    job_id: int = Field(ge=1)


class NoResultRequest(ResumeRequest):
    channel_checked: Literal[True]
    confirmed: Literal[True]
    reason: str = Field(min_length=8, max_length=1000, pattern=r"\S.{6,}\S")


class LinkRemoteRequest(NoResultRequest):
    remote_task_id: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


@router.get(PREFIX)
async def get_recovery(segment_id: int, episode: EpisodeDep, session: SessionDep, user: CurrentUser):
    return await service.recovery(session, user.id, project_id=episode.project_id,
                                  episode_id=episode.id, segment_id=segment_id)


@router.post(PREFIX + "/resume")
async def resume_recovery(segment_id: int, payload: ResumeRequest, episode: EpisodeDep,
                          session: SessionDep, user: CurrentUser):
    result = await service.resume(session, user.id, project_id=episode.project_id,
                                  episode_id=episode.id, segment_id=segment_id, job_id=payload.job_id)
    await session.commit()
    return result


@router.post(PREFIX + "/confirm-no-result")
async def confirm_no_result(segment_id: int, payload: NoResultRequest, episode: EpisodeDep,
                            session: SessionDep, user: CurrentUser):
    result = await service.confirm_no_result(session, user.id, project_id=episode.project_id,
        episode_id=episode.id, segment_id=segment_id, job_id=payload.job_id, reason=payload.reason)
    await session.commit()
    return result


@router.post(PREFIX + "/link-remote-task")
async def link_remote_task(segment_id: int, payload: LinkRemoteRequest, episode: EpisodeDep,
                           session: SessionDep, user: CurrentUser):
    result = await service.link_remote_task(session, user.id, project_id=episode.project_id,
        episode_id=episode.id, segment_id=segment_id, job_id=payload.job_id,
        remote_task_id=payload.remote_task_id, reason=payload.reason)
    await session.commit()
    return result
