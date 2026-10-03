"""剧本资产拆解相关 Schema。"""
from app.core.creation_limits import MAX_EPISODES

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.creation_breakdown_planning import (
    REQUIREMENT_RESULT_KEYS,
    normalized_asset_key,
)


class ScriptAssetUsageRecord(BaseModel):
    episode_number: int = Field(ge=1, le=MAX_EPISODES)
    scene: str = Field(default="", max_length=500)
    source_excerpt: str = Field(default="", max_length=2000)
    performance: str = Field(default="", max_length=2000)
    timing: str = Field(default="", max_length=500)
    needs_review: bool = False

    @field_validator("scene", "source_excerpt", "performance", "timing", mode="before")
    @classmethod
    def normalize_unknown_text(cls, value: Any) -> Any:
        return "" if value is None else value


class ScriptAssetItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    episode_numbers: list[int] = Field(default_factory=list, max_length=MAX_EPISODES)
    description: str = Field(min_length=1, max_length=4000)
    prompt_anchor: str = Field(default="", max_length=4000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    usage_records: list[ScriptAssetUsageRecord] = Field(default_factory=list, max_length=200)


class ScriptAssetUsageLink(ScriptAssetUsageRecord):
    asset_key: str = Field(min_length=1, max_length=512)


class ScriptAssetBreakdownResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    characters: list[ScriptAssetItem] = Field(default_factory=list, max_length=50)
    costumes: list[ScriptAssetItem] = Field(default_factory=list, max_length=100)
    scenes: list[ScriptAssetItem] = Field(default_factory=list, max_length=100)
    props: list[ScriptAssetItem] = Field(default_factory=list, max_length=100)
    character_voices: list[ScriptAssetItem] = Field(default_factory=list, max_length=50)
    music: list[ScriptAssetItem] = Field(default_factory=list, max_length=100)
    ambience: list[ScriptAssetItem] = Field(default_factory=list, max_length=100)
    sound_effects: list[ScriptAssetItem] = Field(default_factory=list, max_length=200)
    usage_records: list[ScriptAssetUsageLink] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def attach_layered_usage_records(self):
        assets: dict[str, ScriptAssetItem] = {}
        for requirement_type, result_key in REQUIREMENT_RESULT_KEYS.items():
            for item in getattr(self, result_key):
                attributes = dict(item.attributes)
                asset_key = str(attributes.get("asset_key") or "").strip() or normalized_asset_key(
                    requirement_type, item.name
                )
                attributes["asset_key"] = asset_key
                item.attributes = attributes
                assets[asset_key] = item
        for usage in self.usage_records:
            target = assets.get(usage.asset_key)
            if target is None:
                raise ValueError(f"使用记录引用了不存在的资产键：{usage.asset_key}")
            record = ScriptAssetUsageRecord.model_validate(
                usage.model_dump(exclude={"asset_key"})
            )
            if record not in target.usage_records:
                target.usage_records.append(record)
        for item in assets.values():
            item.episode_numbers = sorted(set(item.episode_numbers) | {
                record.episode_number for record in item.usage_records
            })
        return self

class ScriptAssetBreakdownRequest(BaseModel):
    requirement_types: list[Literal["character", "costume", "scene", "prop", "character_voice", "music", "ambience", "sound_effect"]] = Field(default_factory=list, max_length=8)
    episode_numbers: list[int] = Field(default_factory=list, max_length=MAX_EPISODES)

    @field_validator("requirement_types")
    @classmethod
    def unique_requirement_types(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("episode_numbers")
    @classmethod
    def valid_episode_numbers(cls, value: list[int]) -> list[int]:
        if any(number < 1 or number > MAX_EPISODES for number in value):
            raise ValueError("拆解分集必须在 1 到 300 之间")
        return sorted(set(value))


class ScriptAssetCandidateUpdate(BaseModel):
    selected: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    aliases: list[str] | None = Field(default=None, max_length=20)
    episode_numbers: list[int] | None = Field(default=None, max_length=MAX_EPISODES)
    matched_asset_id: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("资产名称不能为空")
        return value.strip() if value is not None else value

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned) > 20:
            raise ValueError("资产别名不能超过 20 个")
        return cleaned

    @field_validator("episode_numbers")
    @classmethod
    def clean_episode_numbers(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(number < 1 or number > MAX_EPISODES for number in value):
            raise ValueError("资产出现集数必须在 1 到 300 之间")
        return sorted(set(value)) if value is not None else value


class ScriptAssetCandidateMergeRequest(BaseModel):
    target_candidate_id: int = Field(ge=1)
