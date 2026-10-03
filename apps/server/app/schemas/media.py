"""M6C 全局媒体作品查询响应。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class MediaFileOut(ORMModel):
    id: int
    project_id: int | None
    owner_id: int
    kind: str
    source: str
    original_name: str | None
    mime_type: str | None
    size: int | None
    width: int | None
    height: int | None
    duration: float | None
    hash: str | None
    linked_project_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MediaProjectLinkCreate(BaseModel):
    project_id: int = Field(ge=1)


class EpisodeExportCreate(BaseModel):
    background_audio_media_id: int | None = Field(default=None, ge=1)
    background_audio_volume: float = Field(default=0.3, ge=0, le=1)
    include_subtitles: bool = True


class GenerationHistoryOut(BaseModel):
    job_id: int
    project_id: int | None
    project_name: str | None
    asset_id: int | None
    asset_name: str | None
    media_file_id: int | None
    job_type: str
    status: str
    provider: str | None
    model: str | None
    prompt: str | None
    parameters: dict[str, Any]
    cost_estimate: int | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
