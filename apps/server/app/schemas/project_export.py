"""Episode export and editor package schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.creation import SceneShotDraftContent

class EpisodeExportStartRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    expected_snapshot_fingerprint: str = Field(min_length=64, max_length=64)


class EpisodeExportPreflightSegment(BaseModel):
    segment_id: int
    order: int
    title: str | None = None
    video_version_id: int | None = None
    media_file_id: int | None = None
    trim_in: float
    timeline_duration: float
    timeline_start: float
    timeline_end: float


class EpisodeExportPreflightIssue(BaseModel):
    code: str
    message: str
    segment_id: int | None = None


class EpisodeExportPreflightOut(BaseModel):
    status: Literal["ready", "blocked"]
    episode_id: int
    plan_id: int | None = None
    plan_version: int | None = None
    plan_revision: int | None = None
    production_revision: int
    snapshot_fingerprint: str
    total_duration: float
    segments: list[EpisodeExportPreflightSegment] = Field(default_factory=list)
    output_spec: dict[str, Any] = Field(default_factory=dict)
    subtitle_count: int = 0
    dialogue_audio_count: int = 0
    background_music: bool = False
    ambience_count: int = 0
    sfx_count: int = 0
    audio_mix_order: list[str] = Field(default_factory=list)
    issues: list[EpisodeExportPreflightIssue] = Field(default_factory=list)
    requires_confirmation: bool = True


class EpisodeExportVersionOut(BaseModel):
    version: int = Field(ge=1)
    job_id: int = Field(ge=1)
    media_file_id: int = Field(ge=1)
    media_url: str
    original_name: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None
    completed_at: datetime
    is_current: bool = False
    available: bool = True
    snapshot_fingerprint: str | None = None
    plan_version: int | None = None
    plan_revision: int | None = None
    output_spec: dict[str, Any] = Field(default_factory=dict)


class EpisodeEngineeringPackageStartRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]
    expected_package_fingerprint: str = Field(min_length=64, max_length=64)


class EpisodeEngineeringPackageSegment(BaseModel):
    segment_id: int
    order: int
    title: str | None = None
    video_version_id: int
    media_file_id: int | None = None
    timeline_duration: float
    trim_in: float
    available: bool


class EpisodeEngineeringPackagePreflightOut(BaseModel):
    status: Literal["ready", "blocked"]
    project_id: int
    project_name: str
    episode_id: int
    episode_number: int
    episode_title: str | None = None
    export_job_id: int | None = None
    final_media_file_id: int | None = None
    package_fingerprint: str
    export_snapshot_fingerprint: str | None = None
    output_spec: dict[str, Any] = Field(default_factory=dict)
    total_duration: float = 0
    subtitle_count: int = 0
    segments: list[EpisodeEngineeringPackageSegment] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    issues: list[EpisodeExportPreflightIssue] = Field(default_factory=list)


class EpisodeEngineeringPackageVersionOut(BaseModel):
    version: int = Field(ge=1)
    job_id: int = Field(ge=1)
    media_file_id: int = Field(ge=1)
    original_name: str | None = None
    size: int | None = None
    hash: str | None = None
    created_at: datetime
    available: bool = True
    package_fingerprint: str | None = None


class EpisodePremiereXmlStartRequest(EpisodeEngineeringPackageStartRequest):
    pass


class EpisodePremiereXmlPreflightOut(EpisodeEngineeringPackagePreflightOut):
    source_package_fingerprint: str
    audio_count: int = 0


class EpisodePremiereXmlVersionOut(EpisodeEngineeringPackageVersionOut):
    application_validation: Literal["not_run", "passed", "failed"] = "not_run"


class EpisodeJianyingDraftStartRequest(EpisodeEngineeringPackageStartRequest):
    pass


class JianyingInstalledVersionOut(BaseModel):
    version: str
    runnable: bool
    layout: Literal["complete", "delta_cache", "incomplete"]
    file_count: int = Field(ge=0)


class JianyingInstallationOut(BaseModel):
    status: Literal[
        "target_available",
        "reference_validated",
        "version_unvalidated",
        "incomplete_only",
        "not_detected",
    ]
    active_version: str | None = None
    release_type: str | None = None
    target_exact_match_available: bool = False
    active_version_reference_validated: bool = False
    reference_validated_versions: list[str] = Field(default_factory=list)
    installed_versions: list[JianyingInstalledVersionOut] = Field(default_factory=list)
    message: str


class EpisodeJianyingDraftPreflightOut(EpisodeEngineeringPackagePreflightOut):
    source_package_fingerprint: str
    target_app: str
    target_version: str
    installation: JianyingInstallationOut


class EpisodeJianyingDraftVersionOut(EpisodeEngineeringPackageVersionOut):
    application_validation: Literal["not_run", "passed", "failed"] = "not_run"
    target_version: str


class EpisodeSceneShotApplyRequest(BaseModel):
    job_id: int = Field(ge=1)
    expected_script_revision: int = Field(ge=0)
    content: SceneShotDraftContent


class EpisodeSceneShotApplyOut(BaseModel):
    project_id: int
    episode_id: int
    scene_count: int
    shot_count: int
    asset_usage_count: int


