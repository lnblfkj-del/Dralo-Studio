"""E3 no-chat episode director contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DirectorShotProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: int = Field(ge=1)
    duration: float = Field(gt=0, le=30)
    shot_size: str = Field(default="", max_length=64)
    camera_angle: str = Field(default="", max_length=64)
    camera_movement: str = Field(default="", max_length=120)
    action: str = Field(min_length=1, max_length=4000)
    subject: str = Field(default="", max_length=255)
    expression: str = Field(default="", max_length=1000)
    dialogue: str = Field(default="", max_length=4000)
    dialogue_speaker: str = Field(default="", max_length=255)
    dialogue_tone: str = Field(default="", max_length=255)
    audio_note: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def normalize_empty_optional_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key in ("shot_size", "camera_angle", "camera_movement", "subject", "expression",
                    "dialogue", "dialogue_speaker", "dialogue_tone", "audio_note"):
            if key in value and value[key] is None:
                value[key] = ""
        return value


class DirectorSegmentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    shot_ids: list[int] = Field(min_length=1, max_length=20)
    generation_duration: float = Field(gt=0, le=30)
    prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=8000)
    entry_state: str = Field(default="", max_length=4000)
    exit_state: str = Field(default="", max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_empty_optional_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key in ("negative_prompt", "entry_state", "exit_state"):
            if key in value and value[key] is None:
                value[key] = ""
        if "parameters" in value and value["parameters"] is None:
            value["parameters"] = {}
        return value


class DirectorModelOutput(BaseModel):
    """The only model output accepted by the director finalizer."""

    model_config = ConfigDict(extra="forbid")

    shots: list[DirectorShotProposal] = Field(min_length=1, max_length=300)
    segments: list[DirectorSegmentProposal] = Field(min_length=1, max_length=100)
    continuity_issues: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("continuity_issues", mode="before")
    @classmethod
    def normalize_continuity_notes(cls, value: Any) -> Any:
        # Providers also return diagnostic objects. Only their text is advisory;
        # asset IDs and blocking severity must still come from server validation.
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [item.get("message", item) if isinstance(item, dict) else item for item in value]
        return value


class EpisodeDirectorPlanRequest(BaseModel):
    auto_prepare: bool = False
    planner_model_id: int = Field(ge=1)
    video_model_id: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    mode: Literal["replan_episode", "optimize_segment"] = "replan_episode"
    selected_segment_ids: list[int] = Field(default_factory=list, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def mode_arguments(self):
        if self.mode == "optimize_segment" and len(self.selected_segment_ids) != 1:
            raise ValueError("单片段优化必须指定且只能指定一个片段")
        if len(self.selected_segment_ids) != len(set(self.selected_segment_ids)):
            raise ValueError("目标片段不能重复")
        return self


class EpisodeDirectorApplyRequest(BaseModel):
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]


class SegmentPlanAdjustRequest(BaseModel):
    operation: Literal["split", "merge", "reorder"]
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]
    segment_ids: list[int] = Field(default_factory=list, max_length=100)
    after_shot_id: int | None = Field(default=None, ge=1)
    ordered_segment_ids: list[int] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def operation_arguments(self):
        if self.operation == "split" and (len(self.segment_ids) != 1 or self.after_shot_id is None):
            raise ValueError("拆分需要一个片段和拆分位置")
        if self.operation == "merge" and len(self.segment_ids) < 2:
            raise ValueError("合并至少需要两个相邻片段")
        if self.operation == "reorder" and not self.ordered_segment_ids:
            raise ValueError("重新编排需要完整片段顺序")
        return self


class SegmentLifecycleRequest(BaseModel):
    operation: Literal["add", "copy", "archive", "restore", "insert_before", "insert_after", "delete"]
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]
    segment_id: int = Field(ge=1)
    shot_ids: list[int] = Field(default_factory=list, max_length=50)
    title: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def operation_arguments(self):
        if self.operation == "add" and not self.shot_ids:
            raise ValueError("新增片段必须选择从原片段移出的分镜")
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("新增片段不能重复选择分镜")
        return self


class DirectorCapabilityOut(BaseModel):
    provider_model_id: int
    model_id: str
    name: str
    durations: list[float]
    aspect_ratios: list[str]
    resolutions: list[str]
    multi_shot: bool
    max_shots_per_segment: int
    max_reference_images: int
    supports_first_frame: bool
    supports_last_frame: bool
    supports_reference_images: bool
    supports_audio: bool
    supports_dialogue: bool
    pricing_snapshot: dict[str, Any]


class DirectorContinuityOut(BaseModel):
    plan_id: int
    status: Literal["passed", "warning", "blocked"]
    issues: list[dict[str, Any]]
