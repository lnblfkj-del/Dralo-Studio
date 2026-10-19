"""Explicit selection, preview confirmation, then one atomic batch submission."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.schemas.job import JobOut
from app.services import segment_selected_video_service
from app.services.job_video_batch_batch import start_episode_video_batch

router = APIRouter(tags=["projects"])
PREFIX = "/{project_id}/episodes/{episode_id}/production/selected-videos"


class Selection(BaseModel):
    segment_ids: list[int] = Field(min_length=1, max_length=100)
    provider_model_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("segment_ids")
    @classmethod
    def valid_selection(cls, value):
        if any(item <= 0 for item in value) or len(value) != len(set(value)):
            raise ValueError("请选择不重复的有效片段")
        return sorted(value)


class ConfirmSelection(Selection):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)
    expected_plan_id: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=0)
    expected_selection_fingerprint: str = Field(min_length=64, max_length=64)


@router.post(PREFIX + "/plan")
async def plan_selected(payload: Selection, episode: EpisodeDep, session: SessionDep, user: CurrentUser):
    return await segment_selected_video_service.preview(session, user.id, project_id=episode.project_id,
                                                        episode_id=episode.id, **payload.model_dump())


@router.post(PREFIX + "/start", response_model=JobOut, status_code=201)
async def start_selected(payload: ConfirmSelection, episode: EpisodeDep, session: SessionDep, user: CurrentUser):
    data = payload.model_dump(exclude={"confirmed", "segment_ids"})
    job = await start_episode_video_batch(session, user.id, project_id=episode.project_id,
                                         episode_id=episode.id, regenerate=True,
                                         selected_segment_ids=payload.segment_ids, **data)
    await session.commit()
    return JobOut.model_validate(job)
