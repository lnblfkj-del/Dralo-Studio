"""Scoped, provider-free recovery inspection and original-job resume."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.services import segment_video_recovery_service as service

router = APIRouter(tags=["projects"])
PREFIX = "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/video-recovery"


class ResumeRequest(BaseModel):
    job_id: int = Field(ge=1)


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
