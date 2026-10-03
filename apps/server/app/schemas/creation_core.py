"""创作入口、会话和阶段审核的基础 Schema。"""
import re
from app.core.creation_limits import MAX_EPISODES, MAX_SOURCE_CHARACTERS

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.schemas.common import ORMModel
from app.schemas.creation_outline import EpisodeOutlineContent, EpisodeOutlineDraftContent
from app.schemas.creation_story import StoryBibleContent
from app.schemas.narrative_spec import NarrativeSpec
from app.services.project_source_service import summarize_project_source


class CreationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["upload", "write", "idea", "blank"] | None = None
    brief: str = Field(default="", max_length=100000)
    reference_name: str = Field(default="", max_length=255)
    reference_text: str = Field(default="", max_length=MAX_SOURCE_CHARACTERS)
    style_id: str = Field(default="default", min_length=1, max_length=128)
    custom_style: str = Field(default="", max_length=2000)
    aspect_ratio: Literal["default", "16:9", "21:9", "9:16", "1:1", "4:3", "3:4"] = "default"
    episode_count: int = Field(default=10, ge=1, le=MAX_EPISODES)
    episode_duration: int = Field(default=90, ge=1, le=3600)
    narrative_spec: NarrativeSpec | None = None
    adapt_source: bool = False
    market: Literal["domestic", "overseas"] = "domestic"
    import_analysis: dict[str, Any] = Field(default_factory=dict)
    market_research_run_id: int | None = Field(default=None, ge=1)
    market_idea_index: int | None = Field(default=None, ge=0)
    market_source_ids: list[int] = Field(default_factory=list, max_length=12)
    market_idea_title: str = Field(default="", max_length=120)
    market_idea_snapshot: dict[str, Any] = Field(default_factory=dict)
    market_source_snapshot: list[dict[str, Any]] = Field(default_factory=list, max_length=12)

    @field_validator("style_id")
    @classmethod
    def valid_style_id(cls, value: str) -> str:
        legacy = {"retro", "palace", "noir", "romance", "youth", "everyday", "ink", "anime", "storybook", "clay", "fantasy", "scifi"}
        if value not in {"default", "custom", *legacy} and not re.fullmatch(r"preset:[1-9][0-9]*", value):
            raise ValueError("视觉风格不存在")
        return value

    @model_validator(mode="after")
    def custom_style_required(self):
        if self.style_id == "custom" and not self.custom_style.strip():
            raise ValueError("请填写自定义风格提示词")
        has_run = self.market_research_run_id is not None
        has_idea = self.market_idea_index is not None
        if has_run != has_idea:
            raise ValueError("市场探查记录与创意编号必须同时提供")
        if has_run and self.source_type not in {"write", "idea"}:
            raise ValueError("市场创意只能交接到剧本创作")
        if not has_run and (self.market_idea_snapshot or self.market_source_snapshot):
            raise ValueError("市场来源快照必须关联市场探查记录")
        return self


class ProjectSourceOut(BaseModel):
    kind: Literal["upload", "write", "market", "blank"]
    raw_source_type: str
    source_preserved: bool
    reference_name: str | None = None
    original_char_count: int = 0
    import_mode: str | None = None
    import_confidence: str | None = None
    market_research_run_id: int | None = None
    market_idea_index: int | None = None
    market_idea_title: str | None = None
    market_source_ids: list[int] = Field(default_factory=list)
    market_sources: list[dict[str, Any]] = Field(default_factory=list)


class ProjectFromBrief(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    creation_settings: CreationSettings

    @model_validator(mode="after")
    def content_required(self):
        if not self.name.strip():
            raise ValueError("项目名称不能为空")
        if not (self.creation_settings.brief.strip() or self.creation_settings.reference_text.strip()):
            raise ValueError("请填写创作要求或上传参考文件")
        return self


class ProjectFromIdea(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    brief: str = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def content_required(self):
        if not self.name.strip():
            raise ValueError("项目名称不能为空")
        if not self.brief.strip():
            raise ValueError("请填写故事点子")
        return self


class CreationSessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    brief: str = Field(min_length=1, max_length=100000)
    settings: dict[str, Any] = Field(default_factory=dict)


class CreationMessageOut(ORMModel):
    id: int
    role: str
    message_type: str
    content: str
    sequence: int
    job_id: int | None
    parameters: dict[str, Any]
    created_at: datetime


class CreationArtifactOut(ORMModel):
    content_loaded: bool = True
    id: int
    artifact_type: str
    version: int
    revision: int
    status: str
    content: dict[str, Any]
    source_job_id: int | None
    created_at: datetime
    updated_at: datetime


class CreationSessionOut(BaseModel):
    workflow_progress: dict[str, Any] | None = None
    id: int
    owner_id: int
    project_id: int | None
    title: str
    brief: str
    settings: dict[str, Any]
    status: str
    active_job_id: int | None
    active_job_target: str | None
    active_job_status: str | None
    active_job_progress: int | None
    latest_job_id: int | None
    latest_job_target: str | None
    latest_job_status: str | None
    latest_job_error: str | None
    messages: list[CreationMessageOut]
    artifacts: list[CreationArtifactOut]
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def source(self) -> ProjectSourceOut:
        return ProjectSourceOut.model_validate(summarize_project_source(self.settings))


class StoryBibleUpdate(BaseModel):
    content: StoryBibleContent


class StoryBibleVersionSave(StoryBibleUpdate):
    expected_revision: int = Field(ge=0)


class StoryBibleConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class EpisodeOutlineUpdate(BaseModel):
    content: EpisodeOutlineDraftContent
    expected_revision: int | None = Field(default=None, ge=0)


class EpisodeOutlineConfirm(BaseModel):
    content: EpisodeOutlineContent | None = None
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def content_and_revision_together(self):
        if (self.content is None) != (self.expected_revision is None):
            raise ValueError("内容与版本号必须同时提供")
        return self


class ReferenceChunkOut(BaseModel):
    id: int
    title: str
    char_count: int
    preview: str


class ArtifactRestoreRequest(BaseModel):
    artifact_type: Literal["story_bible", "episode_outline"]
    expected_current_id: int | None = None
    expected_revision: int | None = Field(default=None, ge=0)
