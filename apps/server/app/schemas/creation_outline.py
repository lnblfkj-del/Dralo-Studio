"""分集大纲相关的创作 Schema。"""
from app.core.creation_limits import MAX_EPISODES

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from app.schemas.outline_rich_text import synopsis_document_text, validate_synopsis_document


class EpisodeOutlineGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_story_artifact_id: int = Field(ge=1)
    expected_story_revision: int = Field(ge=0)


class EpisodeOutlineItem(BaseModel):
    synopsis_document: dict[str, Any] | None = None
    outline_key: str | None = Field(default=None, min_length=1, max_length=64)
    linked_episode_id: int | None = Field(default=None, ge=1)
    unit_id: str | None = Field(default=None, min_length=1, max_length=64)
    number: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(min_length=1, max_length=255)
    synopsis: str = Field(min_length=1, max_length=20000)
    dramatic_goal: str = Field(min_length=1, max_length=1000)
    cliffhanger: str = Field(default="", max_length=1000)
    characters: list[str] = Field(default_factory=list, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)

    @field_validator("cliffhanger", mode="before")
    @classmethod
    def empty_cliffhanger(cls, value):
        return "" if value is None else value

    @field_validator("characters")
    @classmethod
    def unique_characters(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len({item.casefold() for item in cleaned}) != len(cleaned):
            raise ValueError("本集登场角色不能重复")
        return cleaned

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "characters" not in self.model_fields_set:
            data.pop("characters", None)
        return data

    @field_validator("synopsis_document")
    @classmethod
    def safe_document(cls, value):
        return validate_synopsis_document(value) if value is not None else None

    @model_validator(mode="after")
    def consistent_document(self):
        if self.synopsis_document is not None and synopsis_document_text(self.synopsis_document) != self.synopsis:
            raise ValueError("梗概纯文本与富文本内容不一致")
        return self


class EpisodeOutlineContent(BaseModel):
    episodes: list[EpisodeOutlineItem] = Field(min_length=1, max_length=MAX_EPISODES)

    @model_validator(mode="after")
    def sequential_episode_numbers(self):
        if [item.number for item in self.episodes] != list(range(1, len(self.episodes) + 1)):
            raise ValueError("分集编号必须从 1 开始连续排列")
        keys = [item.outline_key for item in self.episodes if item.outline_key]
        if len(keys) != len(set(keys)):
            raise ValueError("分集标识不能重复")
        return self


class EpisodeOutlineDraftItem(EpisodeOutlineItem):
    synopsis: str = Field(default="", max_length=20000)
    dramatic_goal: str = Field(default="", max_length=1000)


class EpisodeOutlineDraftContent(BaseModel):
    episodes: list[EpisodeOutlineDraftItem] = Field(default_factory=list, max_length=MAX_EPISODES)

    @model_validator(mode="after")
    def sequential_episode_numbers(self):
        if [item.number for item in self.episodes] != list(range(1, len(self.episodes) + 1)):
            raise ValueError("分集编号必须从 1 开始连续排列")
        keys = [item.outline_key for item in self.episodes if item.outline_key]
        if len(keys) != len(set(keys)):
            raise ValueError("分集标识不能重复")
        return self


class EpisodeOutlineOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add", "delete", "restore", "purge", "move", "duplicate"]
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=64)
    outline_key: str | None = Field(default=None, min_length=1, max_length=64)
    position: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    title: str = Field(default="未命名分集", min_length=1, max_length=255)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)
    count: int = Field(default=1, ge=1, le=MAX_EPISODES)

    @model_validator(mode="after")
    def required_target(self):
        if self.action != "add" and not self.outline_key:
            raise ValueError("本操作必须指定分集标识")
        if self.action == "move" and self.position is None:
            raise ValueError("调整顺序必须指定目标位置")
        if self.action != "add" and self.count != 1:
            raise ValueError("只有新增分集支持设置数量")
        return self
