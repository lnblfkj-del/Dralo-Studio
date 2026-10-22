"""Versioned video settings. Client edits cannot manufacture test evidence."""

import json
import re
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

    @model_validator(mode="after")
    def coherent_generation_methods(self):
        # Apply to edits/imports only. Persisted configurations and frozen jobs
        # remain readable until an administrator explicitly corrects them.
        for mode in self.specification.modes:
            if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", mode.aspect_ratio):
                raise ValueError(f"{mode.key}: 画幅须为单个比例, 例如 16:9")
            limits = {r.role: r for r in mode.reference_limits if r.maximum or r.minimum}
            fixed = {role for role, limit in limits.items() if limit.minimum == limit.maximum == 1}
            if mode.input_mode not in {"text", "single_image", "first_frame", "first_last_frame", "multi_reference"}:
                raise ValueError(f"{mode.key}: 请选择支持的生成方式")
            if mode.input_mode == "text" and (limits or mode.max_total_references):
                raise ValueError(f"{mode.key}: 文字生成不能包含参考素材")
            if mode.input_mode == "single_image" and (
                "image" not in fixed or {"first_frame", "last_frame"} & limits.keys()
            ):
                raise ValueError(f"{mode.key}: 单图参考需要且只接受 1 张参考图片")
            if mode.input_mode == "first_frame" and ("first_frame" not in fixed or "last_frame" in limits):
                raise ValueError(f"{mode.key}: 首帧生成需要 1 张首帧图片, 不能配置尾帧")
            if mode.input_mode == "first_last_frame" and not {"first_frame", "last_frame"} <= fixed:
                raise ValueError(f"{mode.key}: 首尾帧生成需要首帧、尾帧各 1 张")
            if mode.input_mode == "multi_reference" and (
                "image" not in limits or limits["image"].minimum < 1
                or {"first_frame", "last_frame"} & limits.keys()
            ):
                raise ValueError(f"{mode.key}: 多图参考需要参考图片, 首尾帧请使用对应生成方式")
        return self

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
