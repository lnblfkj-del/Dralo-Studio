from fastapi import APIRouter

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.schemas.video_quality_review import ReviewDecision, ReviewRequest, ReviewStart
from app.services import video_quality_review as service

router = APIRouter(tags=["projects"])
PREFIX = "/{project_id}/episodes/{episode_id}/video-quality"


@router.get(PREFIX)
async def read(episode: EpisodeDep, session: SessionDep):
    return {"models": await service.models(session),
            "jobs": [service.serialize(job) for job in await service.history(session, episode)]}


@router.post(PREFIX + "/preview")
async def preview(payload: ReviewRequest, episode: EpisodeDep, session: SessionDep):
    return await service.snapshot(session, episode, payload)


@router.post(PREFIX + "/start")
async def start(payload: ReviewStart, episode: EpisodeDep, session: SessionDep, user: CurrentUser):
    result = await service.start(session, episode, user.id, payload)
    await session.commit()
    return result


@router.patch(PREFIX + "/{job_id}/decision")
async def decision(job_id: int, payload: ReviewDecision, episode: EpisodeDep, session: SessionDep):
    result = await service.decision(session, episode, job_id, payload)
    await session.commit()
    return result
