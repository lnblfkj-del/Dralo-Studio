"""Versioned video settings. Client edits cannot manufacture test evidence."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.episode_planning import VideoMode
from app.schemas.video_connection import VideoConnection


class VideoSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modes: list[VideoMode] = Field(min_length=1, max_length=64)
    supports_negative_prompt: bool | None = None

    @field_validator("modes", mode="before")
    @classmethod
    def parse_modes(cls, value):
        if isinstance(value, list):
            return [item if isinstance(item, VideoMode) else VideoMode.model_validate_json(json.dumps(item))
                    for item in value]
        return value

    @model_validator(mode="after")
    def unique_modes(self):
        if len({mode.key for mode in self.modes}) != len(self.modes):
            raise ValueError("视频设置组标识重复")
        identities = [(m.input_mode, m.aspect_ratio, m.resolution) for m in self.modes]
        if len(set(identities)) != len(identities):
            raise ValueError("同一生成方式、画幅和分辨率只能配置一组限制")
        return self


class VideoConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["video-model.v1"] = "video-model.v1"
    integration: VideoConnection | None = None
    specification: VideoSpecification
    generation_defaults: dict[str, Any] = Field(default_factory=dict)
    planning_policy: dict[str, Any] = Field(default_factory=dict)
    transport_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generation_defaults", "planning_policy", "transport_params")
    @classmethod
    def finite_json(cls, value):
        json.dumps(value, allow_nan=False)
        return value


class VideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["video-model.v1"] = "video-model.v1"
    integration: VideoConnection | None = None
    specification: VideoSpecification | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    declaration: dict[str, Any] = Field(default_factory=dict)
    generation_defaults: dict[str, Any] = Field(default_factory=dict)
    planning_policy: dict[str, Any] = Field(default_factory=dict)
    transport_params: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    pending_settings: dict[str, Any] = Field(default_factory=dict)
    provenance: Literal["model-settings", "legacy-matrix", "legacy-flat", "legacy-incomplete"] = "model-settings"
