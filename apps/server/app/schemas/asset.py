"""M5 project assets, prompt references, and generated versions."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ASSET_TYPES
from app.schemas.common import ORMModel


class AssetCreate(BaseModel):
    asset_type: str
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("asset_type")
    @classmethod
    def valid_asset_type(cls, value: str) -> str:
        if value not in ASSET_TYPES:
            raise ValueError("不支持的资产类型")
        return value

    @field_validator("name", "slug")
    @classmethod
    def clean_required(cls, value: str) -> str:
        return value.strip().removeprefix("@")


class AssetUpdate(BaseModel):
    asset_type: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    attributes: dict[str, Any] | None = None

    @field_validator("asset_type")
    @classmethod
    def valid_asset_type(cls, value: str | None) -> str | None:
        if value is not None and value not in ASSET_TYPES:
            raise ValueError("不支持的资产类型")
        return value

    @field_validator("name", "slug")
    @classmethod
    def clean_required(cls, value: str | None) -> str | None:
        return value.strip().removeprefix("@") if value is not None else None


class AssetPromptProposalRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    generation_mode: Literal["missing", "regenerate"] = "missing"
    provider_model_id: int | None = Field(default=None, ge=1)
    request_id: str = Field(min_length=8, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirmed: Literal[True]

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("资产 ID 必须为正整数")
        return list(dict.fromkeys(value))


class AssetImageBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_contract: Literal["asset-image-batch.v5"]
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    provider_model_id: int = Field(ge=1)
    generation_mode: Literal["missing", "regenerate"] = "missing"
    negative_prompt: str | None = Field(default=None, max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=128)
    confirmed: Literal[True]

    @field_validator("asset_ids")
    @classmethod
    def unique_batch_asset_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("资产 ID 必须为正整数")
        return list(dict.fromkeys(value))

    @field_validator("negative_prompt")
    @classmethod
    def clean_negative_prompt(cls, value: str | None) -> str | None:
        clean = (value or "").strip()
        return clean or None


class AssetVersionOut(ORMModel):
    id: int
    asset_id: int
    media_file_id: int
    source_job_id: int | None
    version: int
    prompt: str
    negative_prompt: str | None
    parameters: dict[str, Any]
    view_label: str
    view_type: str
    review_status: str
    tags: list[str]
    is_final: bool
    created_at: datetime


class AssetVersionUpdate(BaseModel):
    view_label: str | None = Field(default=None, min_length=1, max_length=120)
    view_type: Literal[
        "base", "appearance", "expression", "state", "angle", "environment", "detail",
        "first_frame", "last_frame", "key_frame", "storyboard_frame", "layout_sheet"
    ] | None = None
    review_status: Literal["candidate", "approved", "archived"] | None = None
    tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("view_label")
    @classmethod
    def clean_view_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class AssetSplitRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    view_type: Literal[
        "base", "appearance", "expression", "state", "angle", "environment", "detail",
        "first_frame", "last_frame", "key_frame", "storyboard_frame"
    ]
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    width: int = Field(ge=2, le=8192, strict=True)
    height: int = Field(ge=2, le=8192, strict=True)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return value.strip()


class AssetSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=100)
    source_token: str = Field(min_length=64, max_length=64)
    regions: list[AssetSplitRegion] = Field(min_length=2, max_length=9)
    confirmed: Literal[True]

    @model_validator(mode="after")
    def distinct_non_overlapping_regions(self):
        if len({region.label for region in self.regions}) != len(self.regions):
            raise ValueError("视图标签不能重复")
        for index, region in enumerate(self.regions):
            for prior in self.regions[:index]:
                if (
                    region.x < prior.x + prior.width
                    and region.x + region.width > prior.x
                    and region.y < prior.y + prior.height
                    and region.y + region.height > prior.y
                ):
                    raise ValueError("视图区域不能重叠，请核对分隔线")
        return self


class AssetSplitInfoOut(BaseModel):
    asset_id: int
    asset_type: str
    version_id: int
    media_id: int
    width: int
    height: int
    source_token: str
    allowed_view_types: list[str]


class AssetReadinessIssue(BaseModel):
    episode_id: int
    episode_number: int
    asset_id: int
    asset_name: str
    code: Literal["missing_final_view", "stale_script_source"]
    message: str


class EpisodeAssetReadinessOut(BaseModel):
    episode_id: int
    episode_number: int
    status: Literal["no_requirements", "ready", "incomplete", "stale"]
    required_asset_ids: list[int] = Field(default_factory=list)
    ready_asset_ids: list[int] = Field(default_factory=list)
    missing_asset_ids: list[int] = Field(default_factory=list)
    issues: list[AssetReadinessIssue] = Field(default_factory=list)


class ProjectAssetReadinessOut(BaseModel):
    project_id: int
    status: Literal["no_requirements", "ready", "incomplete", "stale"]
    can_start_production: bool
    required_assets: int
    ready_assets: int
    missing_assets: int
    episodes: list[EpisodeAssetReadinessOut] = Field(default_factory=list)
    issues: list[AssetReadinessIssue] = Field(default_factory=list)


class AssetOut(ORMModel):
    id: int
    project_id: int | None
    owner_id: int
    asset_type: str
    name: str
    slug: str
    description: str | None
    prompt_anchor: str | None
    attributes: dict[str, Any]
    versions: list[AssetVersionOut] = Field(default_factory=list)
    linked_project_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PromptExpandRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)


class PromptExpandOut(BaseModel):
    prompt: str
    references: list[AssetOut]


class AssetGenerateRequest(BaseModel):
    provider_model_id: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=10000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    view_type: Literal[
        "base", "appearance", "expression", "state", "angle", "environment", "detail",
        "first_frame", "last_frame", "key_frame", "storyboard_frame", "layout_sheet"
    ] | None = None
    view_label: str | None = Field(default=None, min_length=1, max_length=120)


class AssetAudioGenerateRequest(BaseModel):
    provider_model_id: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=10000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class AssetLinkCreate(BaseModel):
    local_slug: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("local_slug")
    @classmethod
    def clean_slug(cls, value: str | None) -> str | None:
        return value.strip().removeprefix("@") if value is not None else None


class AssetUsageCreate(BaseModel):
    shot_id: int = Field(ge=1)
    usage_type: Literal[
        "character", "scene", "prop", "costume", "voice", "video", "canvas", "reference",
        "first_frame", "last_frame", "key_frame", "storyboard_frame"
    ] = "reference"
    asset_version_id: int | None = Field(default=None, ge=1)


class AssetUsageReplace(BaseModel):
    usages: list[AssetUsageCreate] = Field(default_factory=list, max_length=1000)


class AssetUsageOut(ORMModel):
    id: int
    project_id: int
    asset_id: int
    episode_id: int
    episode_number: int
    scene_id: int
    scene_name: str
    shot_id: int
    shot_order: int
    asset_version_id: int | None
    usage_type: str
    created_at: datetime
