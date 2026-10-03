"""Episode production and segment plan schemas."""

from app.core.creation_limits import MAX_EPISODES

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel
from app.schemas.project_core import EpisodeOut

class EpisodeDialogueCue(BaseModel):
    segment_id: int = Field(ge=1)
    shot_id: int = Field(ge=1)
    speaker_asset_id: int | None = Field(default=None, ge=1)
    voice_asset_id: int | None = Field(default=None, ge=1)
    text: str = Field(default="", max_length=4000)
    start_time: float = Field(ge=0, le=36000)
    end_time: float = Field(gt=0, le=36000)
    audio_media_id: int | None = Field(default=None, ge=1)
    audio_mode: Literal["replace", "mix"] = "replace"
    native_dialogue_mix_confirmed: bool = False
    gain: float = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_time <= self.start_time:
            raise ValueError("对白结束时间必须晚于开始时间")
        return self


class EpisodeDialogueCuePreview(BaseModel):
    segment_id: int
    segment_order: int
    segment_title: str | None = None
    shot_id: int
    shot_order: int
    scene_name: str | None = None
    audio_note: str | None = None
    speaker_asset_id: int | None = None
    speaker_name: str | None = None
    voice_asset_id: int | None = None
    voice_asset_name: str | None = None
    text: str
    start_time: float
    end_time: float
    segment_start_time: float
    segment_end_time: float
    audio_media_id: int | None = None
    audio_mode: Literal["replace", "mix"] = "replace"
    native_dialogue_mix_confirmed: bool = False
    gain: float = 1
    audio_name: str | None = None
    audio_duration: float | None = None
    readiness: Literal["unbound", "ready", "too_long", "too_short", "duration_unknown"]


class EpisodeDialogueCueListOut(BaseModel):
    plan_id: int
    plan_revision: int
    items: list[EpisodeDialogueCuePreview] = Field(default_factory=list)


class EpisodeSoundCue(BaseModel):
    cue_id: str = Field(min_length=1, max_length=64)
    kind: Literal["ambience", "sfx"]
    label: str = Field(default="", max_length=120)
    audio_media_id: int = Field(ge=1)
    start_time: float = Field(ge=0, le=36000)
    end_time: float = Field(gt=0, le=36000)
    gain: float = Field(default=1, ge=0, le=2)
    loop: bool = False

    @model_validator(mode="after")
    def valid_sound_cue(self):
        if self.end_time <= self.start_time:
            raise ValueError("声音结束时间必须晚于开始时间")
        if self.kind == "sfx" and self.loop:
            raise ValueError("音效只支持单次播放")
        return self


class EpisodeProductionSettings(BaseModel):
    """跨页面持久化的单集制作偏好；生成参数仍由具体模型能力约束。"""

    aspect_ratio: Literal["project", "16:9", "9:16", "1:1"] = "project"
    resolution: str = Field(default="720p", min_length=2, max_length=32)
    frame_rate: Literal[24, 30] = 24
    default_shot_duration: float = Field(default=4, ge=1, le=30)
    include_subtitles: bool = True
    background_audio_media_id: int | None = Field(default=None, ge=1)
    background_audio_volume: float = Field(default=0.3, ge=0, le=1)
    video_model_id: int | None = Field(default=None, ge=1)
    asset_ids: list[int] = Field(default_factory=list, max_length=200)
    dialogue_cues: list[EpisodeDialogueCue] = Field(default_factory=list, max_length=500)
    sound_cues: list[EpisodeSoundCue] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_asset_ids(self):
        if len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("本集资产不能重复")
        cue_keys = [(item.segment_id, item.shot_id) for item in self.dialogue_cues]
        if len(set(cue_keys)) != len(cue_keys):
            raise ValueError("同一片段分镜不能重复配置对白")
        sound_cue_ids = [item.cue_id for item in self.sound_cues]
        if len(set(sound_cue_ids)) != len(sound_cue_ids):
            raise ValueError("声音条目标识不能重复")
        return self


class EpisodeProductionUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    settings: EpisodeProductionSettings


class EpisodeProductionOut(BaseModel):
    episode: EpisodeOut
    production_id: int | None = None
    workflow_status: Literal[
        "script_missing",
        "script_ready",
        "storyboard_ready",
        "producing",
        "clips_ready",
        "completed",
        "failed",
    ]
    settings: EpisodeProductionSettings = Field(default_factory=EpisodeProductionSettings)
    revision: int = 0
    source_script_revision: int | None = None
    script_dependency_status: Literal["unconfirmed", "current", "stale"] = "unconfirmed"
    script_stale_reason: str | None = None
    scene_count: int = 0
    shot_count: int = 0
    ready_shot_count: int = 0
    failed_shot_count: int = 0
    production_plan_id: int | None = None
    production_plan_version: int | None = None
    production_plan_status: str | None = None
    segment_count: int = 0
    ready_segment_count: int = 0
    failed_segment_count: int = 0
    generation_duration: float = 0
    duration: float = 0
    asset_count: int = 0
    final_media_file_id: int | None = None
    final_media_url: str | None = None
    active_job_id: int | None = None
    active_job_status: str | None = None
    active_job_progress: int = 0
    active_job_result: dict[str, Any] | None = None
    last_error: str | None = None
    can_retry: bool = False


class VideoSegmentCreate(BaseModel):
    segment_status: Literal["pending", "archived"] = "pending"
    lineage_key: str | None = Field(default=None, min_length=1, max_length=64)
    parent_lineage_keys: list[str] = Field(default_factory=list, max_length=20)
    title: str | None = Field(default=None, max_length=255)
    shot_ids: list[int] = Field(default_factory=list, max_length=50)
    generation_duration: float = Field(gt=0, le=30)
    timeline_duration: float | None = Field(default=None, gt=0, le=30)
    trim_in: float = Field(default=0, ge=0, le=30)
    trim_out: float = Field(default=0, ge=0, le=30)
    prompt: str = Field(default="", max_length=20000)
    negative_prompt: str | None = Field(default=None, max_length=10000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    refs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_shots(self):
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("同一片段不能重复引用分镜")
        if len(self.parent_lineage_keys) != len(set(self.parent_lineage_keys)):
            raise ValueError("片段父血缘不能重复")
        return self


class SegmentProductionPlanCreate(BaseModel):
    expected_production_revision: int = Field(ge=0)
    provider_model_id: int | None = Field(default=None, ge=1)
    model_capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: Literal["draft", "confirmed"] = "draft"
    source_type: Literal[
        "ai", "manual", "import", "copy", "split", "merge", "reorder",
        "replan", "optimize_segment", "fill_empty", "optimize_selected", "add", "archive",
        "restore", "text_import",
    ] = "manual"
    parent_plan_id: int | None = Field(default=None, ge=1)
    segments: list[VideoSegmentCreate] = Field(max_length=100)


class SegmentManualPlanCreate(BaseModel):
    expected_production_revision: int = Field(ge=0)
    provider_model_id: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoSegmentShotOut(BaseModel):
    shot_id: int
    order: int
    start_time: float
    end_time: float
    scene_id: int | None = None
    scene_name: str | None = None
    shot_size: str | None = None
    camera_angle: str | None = None
    camera_movement: str | None = None
    action: str | None = None
    dialogue: str | None = None
    audio_note: str | None = None


class SegmentVideoVersionOut(ORMModel):
    id: int
    media_file_id: int
    source_job_id: int | None = None
    legacy_shot_video_version_id: int | None = None
    version: int
    prompt: str
    negative_prompt: str | None = None
    parameters: dict[str, Any]
    is_final: bool
    media_url: str
    candidate_status: Literal[
        "ready", "adopted", "input_stale", "media_unavailable",
        "legacy_unverified", "evidence_invalid",
    ] = "legacy_unverified"
    adoptable: bool = False
    adoption_block_reason: str | None = None
    media_status: Literal["ready", "missing", "invalid"] = "missing"
    input_fingerprint: str | None = None
    script_fingerprint: str | None = None
    provider_model_id: int | None = None


class SegmentVideoVersionSelectRequest(BaseModel):
    expected_plan_revision: int = Field(ge=0)
    expected_input_fingerprint: str = Field(min_length=64, max_length=64)


class VideoSegmentOut(BaseModel):
    script_state: str = "legacy_unreviewed"
    id: int
    lineage_key: str
    parent_lineage_keys: list[str]
    order: int
    title: str | None = None
    generation_duration: float
    timeline_duration: float
    trim_in: float
    trim_out: float
    prompt: str
    negative_prompt: str | None = None
    parameters: dict[str, Any]
    refs: dict[str, Any]
    pricing_estimate: dict[str, Any]
    status: str
    shots: list[VideoSegmentShotOut]
    video_versions: list[SegmentVideoVersionOut]


class SegmentProductionPlanOut(BaseModel):
    id: int
    episode_id: int
    version: int
    source_type: str
    parent_plan_id: int | None = None
    status: str
    source_script_revision: int
    provider_model_id: int | None = None
    model_capability_snapshot: dict[str, Any]
    parameters: dict[str, Any]
    total_timeline_duration: float
    total_generation_duration: float
    revision: int
    segments: list[VideoSegmentOut]
    created_at: datetime
    updated_at: datetime


class EpisodeProductionPlanRequest(BaseModel):
    provider_model_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    regenerate: bool = False


class EpisodeProductionStartRequest(EpisodeProductionPlanRequest):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)
    expected_plan_id: int | None = Field(default=None, ge=1)
    expected_plan_revision: int | None = Field(default=None, ge=0)


class SegmentVideoAttemptRequest(BaseModel):
    provider_model_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class SegmentVideoAttemptStartRequest(SegmentVideoAttemptRequest):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)
    expected_plan_id: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=0)
    expected_video_prompt_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class H3PromptStartRequest(BaseModel):
    video_model_id: int = Field(ge=1)
    text_model_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)


class H3VideoRequest(BaseModel):
    video_model_id: int = Field(ge=1)
    authoring_job_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class H3VideoStartRequest(H3VideoRequest):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    confirm_media_upload: bool = False


class SegmentFirstFramePlanRequest(BaseModel):
    segment_ids: list[int] = Field(min_length=1, max_length=100)
    provider_model_id: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    negative_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("segment_ids")
    @classmethod
    def unique_segment_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("片段 ID 必须为正整数")
        return list(dict.fromkeys(value))


class SegmentFirstFrameBatchRequest(SegmentFirstFramePlanRequest):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    max_cost_cents: int = Field(ge=0, le=100000000)
    expected_plan_id: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=0)


class SegmentFirstFramePlanOut(BaseModel):
    project_id: int
    episode_id: int
    provider_model_id: int
    provider_name: str
    model_id: str
    plan_id: int
    plan_revision: int
    segment_ids: list[int]
    estimated_count: int
    parameters: dict[str, Any]
    pricing_estimate: dict[str, Any]
    requires_confirmation: bool = True


class EpisodeProductionShotIssue(BaseModel):
    segment_id: int | None = None
    shot_ids: list[int] = Field(default_factory=list)
    shot_id: int
    scene_id: int
    order: int
    reason: str


class EpisodeProductionPlanOut(BaseModel):
    project_id: int
    episode_id: int
    provider_model_id: int
    provider_name: str
    model_id: str
    plan_id: int
    plan_version: int
    plan_revision: int
    eligible_segment_ids: list[int]
    skipped_final_segment_ids: list[int]
    eligible_shot_ids: list[int]
    skipped_final_shot_ids: list[int]
    blocked: list[EpisodeProductionShotIssue]
    total_shots: int
    total_segments: int
    total_generation_duration: float
    estimated_count: int
    pricing_estimate: dict[str, Any]
    video_inputs: list[dict[str, Any]] = Field(default_factory=list)
    requires_confirmation: bool = True


class ProjectEpisodeBatchStartRequest(EpisodeProductionStartRequest):
    episode_ids: list[int] = Field(min_length=1, max_length=MAX_EPISODES)

    @model_validator(mode="after")
    def unique_episode_ids(self):
        if len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError("批量分集不能重复")
        return self
