"""Project, episode, and script readiness schemas."""

from app.core.creation_limits import MAX_EPISODES, MAX_SOURCE_CHARACTERS

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from app.schemas.common import ORMModel
from app.schemas.creation import CreationSettings, ProjectSourceOut
from app.services.project_source_service import summarize_project_source



class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    genre: str | None = Field(default=None, max_length=64)
    creation_settings: CreationSettings = Field(default_factory=CreationSettings)


class EpisodeMarker(BaseModel):
    number: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(min_length=1, max_length=255)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    char_count: int = Field(default=0, ge=0)


class ProjectFromScript(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    script: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS)
    creation_settings: CreationSettings = Field(default_factory=CreationSettings)
    episode_markers: list[EpisodeMarker] = Field(default_factory=list, max_length=MAX_EPISODES)
    auto_optimize: bool = False


class ProjectUpdate(BaseModel):
    creation_settings: CreationSettings = Field(default_factory=CreationSettings)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    genre: str | None = Field(default=None, max_length=64)
    cover_url: str | None = Field(default=None, max_length=512)
    status: str | None = Field(default=None, max_length=16)


class ProjectCardParticipant(BaseModel):
    id: int
    name: str


class ProjectCardSummary(BaseModel):
    cover_media_id: int | None = None
    episode_count: int = 0
    completed_episodes: int = 0
    estimated_duration: int = 0
    style_name: str
    participants: list[ProjectCardParticipant] = Field(default_factory=list)


class ProjectOut(ORMModel):
    card_summary: ProjectCardSummary | None = None
    creation_settings: dict[str, Any] = Field(default_factory=dict)
    id: int
    owner_id: int
    name: str
    description: str | None = None
    cover_url: str | None = None
    genre: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def source(self) -> ProjectSourceOut:
        return ProjectSourceOut.model_validate(summarize_project_source(self.creation_settings))


# ---------- Episode ----------


class EpisodeCreate(BaseModel):
    number: int = Field(ge=1, le=MAX_EPISODES)
    title: str | None = Field(default=None, max_length=255)
    synopsis: str | None = None


class EpisodeUpdate(BaseModel):
    expected_script_revision: int | None = Field(default=None, ge=0)
    version_note: str | None = Field(default=None, max_length=120)
    number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    title: str | None = Field(default=None, max_length=255)
    synopsis: str | None = None
    script: str | None = Field(default=None, max_length=100000)
    status: str | None = Field(default=None, max_length=16)
    duration_estimate: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_script_revision(self):
        if "script" in self.model_fields_set and self.expected_script_revision is None:
            raise ValueError("保存正文需要当前版本号，请刷新页面后重试")
        return self


class EpisodeOut(ORMModel):
    script_revision: int
    finalized_script_revision: int | None = None
    script_finalized_at: datetime | None = None
    continuity_review_status: Literal[
        "unchecked", "current", "warning", "conflict", "needs_review"
    ] = "unchecked"
    continuity_review_reason: str | None = None
    id: int
    project_id: int
    owner_id: int
    number: int
    title: str | None = None
    synopsis: str | None = None
    script: str | None = None
    status: str
    duration_estimate: int | None = None
    created_at: datetime
    updated_at: datetime


class ScriptReadinessIssue(BaseModel):
    episode_id: int | None = None
    episode_number: int | None = None
    code: Literal[
        "no_episodes",
        "non_sequential_numbers",
        "missing_script",
        "missing_duration",
        "unconfirmed_revision",
        "stale_revision",
        "continuity_conflict",
        "continuity_stale",
        "screenplay_source_ambiguous",
    ]
    message: str


class ScriptReadinessEpisode(BaseModel):
    episode_id: int
    number: int
    title: str | None = None
    script_revision: int
    finalized_script_revision: int | None = None
    duration_estimate: int | None = None
    status: Literal[
        "missing_script", "missing_duration", "unconfirmed", "confirmed", "stale"
    ]
    continuity_review_status: Literal[
        "unchecked", "current", "warning", "conflict", "needs_review"
    ] = "unchecked"
    continuity_review_reason: str | None = None


class ProjectScriptReadinessOut(BaseModel):
    project_id: int
    status: Literal["no_episodes", "incomplete", "ready", "confirmed", "stale"]
    can_confirm: bool
    total_episodes: int
    confirmed_episodes: int
    confirmed_at: datetime | None = None
    issues: list[ScriptReadinessIssue] = Field(default_factory=list)
    episodes: list[ScriptReadinessEpisode] = Field(default_factory=list)


class ScriptFinalizeEpisodeRequest(BaseModel):
    episode_id: int = Field(ge=1)
    expected_script_revision: int = Field(ge=0)
    duration_estimate: int = Field(ge=1, le=3600)


class ProjectScriptFinalizeRequest(BaseModel):
    confirmed: Literal[True]
    episodes: list[ScriptFinalizeEpisodeRequest] = Field(min_length=1, max_length=MAX_EPISODES)

    @model_validator(mode="after")
    def unique_episode_ids(self):
        ids = [item.episode_id for item in self.episodes]
        if len(ids) != len(set(ids)):
            raise ValueError("确认列表不能包含重复分集")
        return self

