"""Scene, shot, and shot video version schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    order: int = Field(default=0, ge=0)
    location: str | None = Field(default=None, max_length=255)
    time_of_day: str | None = Field(default=None, max_length=64)
    description: str | None = None


class SceneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    order: int | None = Field(default=None, ge=0)
    location: str | None = Field(default=None, max_length=255)
    time_of_day: str | None = Field(default=None, max_length=64)
    description: str | None = None


class SceneOut(ORMModel):
    id: int
    episode_id: int
    owner_id: int
    order: int
    name: str
    location: str | None = None
    time_of_day: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------- Shot ----------


class ShotCreate(BaseModel):
    order: int = Field(default=0, ge=0)
    duration: float | None = Field(default=None, ge=0)
    shot_size: str | None = Field(default=None, max_length=64)
    camera_angle: str | None = Field(default=None, max_length=64)
    camera_movement: str | None = Field(default=None, max_length=120)
    action: str | None = None
    dialogue: str | None = None
    audio_note: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    refs: dict[str, Any] = Field(default_factory=dict)


class ShotUpdate(BaseModel):
    order: int | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0)
    shot_size: str | None = Field(default=None, max_length=64)
    camera_angle: str | None = Field(default=None, max_length=64)
    camera_movement: str | None = Field(default=None, max_length=120)
    action: str | None = None
    dialogue: str | None = None
    audio_note: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    status: str | None = Field(default=None, max_length=16)
    is_locked: bool | None = None
    refs: dict[str, Any] | None = None


class ShotOut(ORMModel):
    id: int
    scene_id: int
    owner_id: int
    order: int
    duration: float | None = None
    shot_size: str | None = None
    camera_angle: str | None = None
    camera_movement: str | None = None
    action: str | None = None
    dialogue: str | None = None
    audio_note: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    status: str
    is_locked: bool
    refs: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ShotVideoVersionOut(ORMModel):
    id: int
    shot_id: int
    media_file_id: int
    media_url: str
    source_job_id: int | None = None
    version: int
    prompt: str
    negative_prompt: str | None = None
    parameters: dict[str, Any]
    is_final: bool
    created_at: datetime
    updated_at: datetime
