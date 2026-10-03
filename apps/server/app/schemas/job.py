"""文本生成任务请求与响应。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


class JobCreate(BaseModel):
    provider_model_id: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=100_000)
    project_id: int | None = Field(default=None, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoJobCreate(BaseModel):
    provider_model_id: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=100_000)
    project_id: int = Field(ge=1)
    negative_prompt: str | None = Field(default=None, max_length=100_000)
    first_frame_media_id: int | None = Field(default=None, ge=1)
    last_frame_media_id: int | None = Field(default=None, ge=1)
    reference_media_ids: list[int] = Field(default_factory=list, max_length=4)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ShotVideoJobCreate(VideoJobCreate):
    project_id: int | None = Field(default=None, ge=1)
    shot_id: int = Field(ge=1)


class BatchVideoJobCreate(BaseModel):
    project_id: int = Field(ge=1)
    provider_model_id: int = Field(ge=1)
    shot_ids: list[int] = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)


class JobOut(ORMModel):
    failure_detail: dict[str, Any] | None = None
    runtime_progress: dict[str, Any] = Field(default_factory=dict)
    continuation_context: dict[str, Any] | None = None
    execution_info: dict[str, Any] | None = None
    video_compilation: dict[str, Any] | None = None
    media_processing: dict[str, Any] | None = None
    agent_execution: dict[str, Any] | None = None
    business_executor: dict[str, Any] | None = None
    production_context: dict[str, Any] | None = None
    resolution: dict[str, Any] | None = None
    retry_allowed: bool = False
    retry_block_reason: str | None = None
    paid_recall_allowed: bool = False
    text_response_recovery: dict[str, Any] | None = None
    execution_policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    id: int
    owner_id: int
    project_id: int | None
    parent_job_id: int | None
    provider_id: int | None
    target_type: str | None
    target_id: int | None
    job_type: str
    status: str
    progress: int
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    attempts: int
    max_attempts: int
    provider: str | None
    model: str | None
    cost_estimate: int | None
    pricing_estimate: dict | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deleted_at: datetime | None
    batch_paused_at: datetime | None


class JobStatsOut(BaseModel):
    total: int
    active: int
    queued: int
    succeeded: int
    failed: int
    cancelled: int
    today_completed: int
    recycled: int


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    offset: int
    limit: int
    stats: JobStatsOut


class BulkJobActionInput(BaseModel):
    job_ids: list[int] = Field(default_factory=list, max_length=5000)
    action: Literal["cancel", "retry", "delete", "restore", "purge"]
    scope: Literal["selection", "filter"] = "selection"
    project_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    job_type: str | None = None
    search: str | None = Field(default=None, max_length=200)
    recycled: bool = False

    @field_validator("job_ids")
    @classmethod
    def normalize_job_ids(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(value))
        if any(job_id < 1 for job_id in normalized):
            raise ValueError("任务编号无效")
        return normalized

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == "selection" and not self.job_ids:
            raise ValueError("请选择至少一个任务")
        if self.action in {"restore", "purge"} and not self.recycled:
            self.recycled = True
        return self


class BulkJobActionItem(BaseModel):
    job_id: int
    outcome: Literal[
        "cancelled", "queued", "deleted", "restored", "purged", "paused", "resumed", "covered_by_parent", "failed"
    ]
    job_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class BulkJobActionOut(BaseModel):
    action: Literal["cancel", "retry", "delete", "restore", "purge"]
    items: list[BulkJobActionItem]
    succeeded: int
    failed: int
    scope: Literal["selection", "filter"]
    matched: int


class JobDeleteOut(BaseModel):
    deleted_ids: list[int]


class JobPurgeOut(BaseModel):
    purged_ids: list[int]


class JobDiagnosticOut(BaseModel):
    job_id: int
    status: str
    error_code: str | None
    error_message: str | None
    attempts: int
    max_attempts: int
    queue_reason: str | None
    worker_online: bool
    cancel_allowed: bool
    retry_allowed: bool
    retry_block_reason: str | None
    paid_recall_allowed: bool = False
    delete_allowed: bool
    execution_info: dict[str, Any] | None
    model_runtime: dict[str, Any] | None = None
