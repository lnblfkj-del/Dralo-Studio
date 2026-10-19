"""HTTP contracts for the new planning workflow, not legacy proposal inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.episode_planning import Digest, Key


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanningCreate(Input):
    planner_model_id: int = Field(gt=0)
    video_model_id: int = Field(gt=0)
    resolution: str | None = Field(default=None, min_length=1, max_length=32)
    request_id: Key
    expected_script_revision: int = Field(ge=0)
    background_music: bool
    acknowledge_text_charges: Literal[True]
    requirements: str = Field(default="", max_length=3000)
    expected_input_fingerprint: Digest

    @field_validator("acknowledge_text_charges", mode="before")
    @classmethod
    def explicit_charge_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit text charge confirmation is required")
        return value


class SavedRecovery(Input):
    expected_fingerprint: Digest


class PlanningPreflight(Input):
    video_model_id: int = Field(gt=0)
    resolution: str | None = Field(default=None, min_length=1, max_length=32)
    background_music: bool
    expected_script_revision: int = Field(ge=0)
    expected_source_fingerprint: Digest


class PaidRecovery(SavedRecovery):
    request_id: Key
    acknowledge_new_charges: Literal[True]
    acknowledge_unknown_submission: bool = False

    @field_validator("acknowledge_new_charges", mode="before")
    @classmethod
    def explicit_charge_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit new charge confirmation is required")
        return value


class SegmentOptimization(PaidRecovery):
    segment_key: Key
    requirements: str = Field(min_length=1, max_length=3000)

    @field_validator("requirements")
    @classmethod
    def meaningful_requirements(cls, value):
        if not value.strip():
            raise ValueError("Optimization requirements are required")
        return value.strip()
