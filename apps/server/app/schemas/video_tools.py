"""Admin-only import, offline validation and paid trial inputs."""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.video_configuration import VideoConfigInput
from app.schemas.video_connection import DataModel, forbid_secrets


class VideoPackage(DataModel):
    schema_version: Literal["video-package.v1"] = "video-package.v1"
    name: str = Field(min_length=1, max_length=80)
    protocol: str = Field(max_length=32)
    video_config: VideoConfigInput

    @model_validator(mode="after")
    def no_credentials(self):
        forbid_secrets(self.video_config.model_dump(mode="json"))
        return self


class PackageLink(DataModel):
    url: str = Field(min_length=10, max_length=2048)


class VideoPresetCreate(DataModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source: str = Field(min_length=1, max_length=200, pattern=r"\S")
    model_ids: list[str] = Field(min_length=1, max_length=64)
    package: VideoPackage

    @model_validator(mode="after")
    def exact_models(self):
        if any(not value.strip() or value != value.strip() or len(value) > 255 for value in self.model_ids):
            raise ValueError("请填写精确的模型名称，不使用空值或前后空格")
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("适用模型不能重复")
        return self


class VideoPresetPreview(DataModel):
    key: str = Field(pattern=r"^(?:(?:model|saved):[1-9][0-9]*|builtin:[a-z][a-z0-9_]{1,79})$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: int = Field(gt=0)
    model_identifier: str = Field(min_length=1, max_length=255)


class VideoRollback(DataModel):
    version_id: int = Field(gt=0)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class VideoAdapterInstall(DataModel):
    package: VideoPackage
    trust_service: Literal[True]


class VideoAdapterState(DataModel):
    enabled: bool


class VideoKeyRotation(DataModel):
    api_key: str = Field(min_length=1, max_length=4096)
    confirm_same_account: Literal[True]


class VideoCheckInput(DataModel):
    api_protocol: str | None = None
    api_base_url: str | None = Field(default=None, max_length=512)
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    video_config: VideoConfigInput | None = None


class VideoTrialInput(DataModel):
    project_id: int = Field(gt=0)
    prompt: str = Field(min_length=1, max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    first_frame_media_id: int | None = Field(default=None, gt=0)
    last_frame_media_id: int | None = Field(default=None, gt=0)
    reference_media_ids: list[int] = Field(default_factory=list, max_length=32)


class VideoTrialStart(VideoTrialInput):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    confirmation_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    accept_unknown_price: bool = False
    max_cost_cents: int = Field(default=0, ge=0, le=1000000)
