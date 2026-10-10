"""HTTP contracts for the new planning workflow, not legacy proposal inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.episode_planning import Digest, Key
from app.schemas.episode_references import SourceReferenceBinding


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReferenceInput(Input):
    media_id: int = Field(gt=0)
    asset_version_id: int | None = Field(default=None, gt=0)
    role: Literal["image", "audio", "video", "first_frame", "last_frame"]
    source_keys: list[Key] = Field(min_length=1, max_length=2000)

    def binding(self) -> SourceReferenceBinding:
        return SourceReferenceBinding.model_validate_json(self.model_dump_json())


class PlanningCreate(Input):
    planner_model_id: int = Field(gt=0)
    video_model_id: int = Field(gt=0)
    mode_key: Key
    request_id: Key
    expected_script_revision: int = Field(ge=0)
    background_music: bool
    acknowledge_text_charges: Literal[True]
    reference_bindings: list[ReferenceInput] = Field(default_factory=list, max_length=2000)
    expected_input_fingerprint: Digest | None = None

    @field_validator("acknowledge_text_charges", mode="before")
    @classmethod
    def explicit_charge_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit text charge confirmation is required")
        return value


class SavedRecovery(Input):
    expected_fingerprint: Digest


class ProductionActivation(SavedRecovery):
    expected_production_revision: int = Field(ge=0)
    expected_active_plan_id: int | None = Field(gt=0)


class PlanningPreflight(Input):
    video_model_id: int = Field(gt=0)
    mode_key: Key
    background_music: bool
    expected_script_revision: int = Field(ge=0)
    expected_source_fingerprint: Digest
    reference_bindings: list[ReferenceInput] = Field(default_factory=list, max_length=2000)


class ModelSwitch(Input):
    request_id: Key
    expected_plan_fingerprint: Digest
    expected_capability_fingerprint: Digest
    video_model_id: int = Field(gt=0)
    mode_key: Key


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
