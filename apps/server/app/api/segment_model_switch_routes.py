"""Model changes inspect the active editable manuscript, never its original run."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import EpisodeDep, SessionDep
from app.services import segment_model_switch_service as service

router = APIRouter()
PREFIX = "/{project_id}/episodes/{episode_id}/production/model-change"


class Check(BaseModel):
    provider_model_id: int = Field(gt=0)
    resolution: str | None = Field(default=None, max_length=40)


class Apply(Check):
    resolution: str = Field(min_length=1, max_length=40)
    request_id: str = Field(min_length=8, max_length=100)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    expected_plan_id: int = Field(gt=0)
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]


@router.post(PREFIX + "/check")
async def check(payload: Check, episode: EpisodeDep, session: SessionDep):
    result = await service.preview(session, episode, **payload.model_dump())
    return {k: v for k, v in result.items() if k not in {"items", "parameters", "status"}}


@router.post(PREFIX + "/apply")
async def apply(payload: Apply, episode: EpisodeDep, session: SessionDep):
    result = await service.apply(session, episode, **payload.model_dump(exclude={"confirmed"}))
    await session.commit()
    return result
