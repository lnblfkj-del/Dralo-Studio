"""Explicit scope for an opt-in, empty-field-only character proposal."""

from typing import Literal
from app.core.creation_limits import MAX_STORY_CHARACTERS

from pydantic import BaseModel, ConfigDict, Field, model_validator


CompletionField = Literal["age", "description", "personality", "appearance", "costume", "voice"]


class CharacterBatchCompletionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_index: int = Field(ge=0, le=MAX_STORY_CHARACTERS - 1)
    character_id: str | None = Field(default=None, min_length=1, max_length=100)
    fields: list[CompletionField] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_fields(self):
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("补全字段不能重复")
        return self


class CharacterBatchCompletionRequest(BaseModel):
    """Explicit roles and empty fields for one reviewable batch proposal."""

    model_config = ConfigDict(extra="forbid")
    artifact_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    targets: list[CharacterBatchCompletionTarget] = Field(min_length=1, max_length=MAX_STORY_CHARACTERS)

    @model_validator(mode="after")
    def unique_targets(self):
        indexes = [target.character_index for target in self.targets]
        if len(indexes) != len(set(indexes)):
            raise ValueError("批量补全角色不能重复")
        ids = [target.character_id for target in self.targets if target.character_id]
        if len(ids) != len(set(ids)):
            raise ValueError("批量补全角色标识不能重复")
        return self


class CharacterOutlineCoverageRequest(BaseModel):
    """Freeze both story and outline revisions for a cast-only AI proposal."""

    model_config = ConfigDict(extra="forbid")
    story_artifact_id: int = Field(ge=1)
    story_expected_revision: int = Field(ge=0)
    outline_artifact_id: int = Field(ge=1)
    outline_expected_revision: int = Field(ge=0)


class StoryOverviewAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)


class EventTimelineAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
