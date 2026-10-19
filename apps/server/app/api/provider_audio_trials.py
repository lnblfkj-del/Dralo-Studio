"""Explicitly confirmed model-local audio tests."""

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.schemas.job import JobDeleteOut, JobOut
from app.services import audio_trial_service as service

router = APIRouter()


class TrialJobOut(JobOut):
    request_id: str


class TrialClearOut(JobDeleteOut):
    deleted_media_ids: list[int]
    retained_media_ids: list[int]


@router.post("/{provider_id}/models/{model_id}/audio-test/plan")
async def plan(provider_id: int, model_id: int, payload: service.TrialInput, session: SessionDep, _admin: AdminUser):
    return await service.plan(session, provider_id, model_id, payload)


@router.post("/{provider_id}/models/{model_id}/audio-test/start", response_model=JobOut)
async def start(provider_id: int, model_id: int, payload: service.TrialStart, session: SessionDep, admin: AdminUser):
    job = await service.start(session, admin.id, provider_id, model_id, payload)
    await session.commit()
    return job


@router.get("/{provider_id}/models/{model_id}/audio-test/history", response_model=list[TrialJobOut])
async def history(provider_id: int, model_id: int, session: SessionDep, admin: AdminUser):
    rows = await service.history(session, admin.id, provider_id, model_id)
    return [{**JobOut.model_validate(job).model_dump(), "request_id": job.payload["parameters"]["audio_request_id"]} for job in rows]


@router.delete("/{provider_id}/models/{model_id}/audio-test/history", response_model=TrialClearOut)
async def clear_history(provider_id: int, model_id: int, session: SessionDep, admin: AdminUser):
    result = await service.clear_history(session, admin.id, provider_id, model_id)
    await session.commit()
    from app.services.audio_trial_cleanup import after_commit
    await after_commit(session)
    return TrialClearOut(**result)
