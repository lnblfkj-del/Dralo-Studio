"""R1 typed additive API; all times are finite seconds, unknown facts remain unknown."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SpeechBinding(StrictContract):
    provider_model_id: int = Field(gt=0)
    voice: str = Field(min_length=1, max_length=255)
    account_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AssetProfile(StrictContract):
    aliases: list[str] = Field(default_factory=list, max_length=30)
    character_role: Literal["lead", "supporting", "extra", "unclassified"] = "unclassified"
    character_form: Literal["single", "fixed_group", "crowd"] = "single"
    group_label: str | None = Field(default=None, max_length=200)
    group_size: int | None = Field(default=None, ge=2, le=100000)
    character_asset_id: int | None = Field(default=None, ge=1)
    linked_character_asset_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    linked_character_asset_id: int | None = Field(default=None, ge=1)
    character_relation_status: Literal["linked", "needs_review", "not_applicable"] | None = None
    age: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    appearance: str | None = Field(default=None, max_length=4000)
    personality: str | None = Field(default=None, max_length=4000)
    goal: str | None = Field(default=None, max_length=4000)
    conflict: str | None = Field(default=None, max_length=4000)
    arc: str | None = Field(default=None, max_length=4000)
    costume: str | None = Field(default=None, max_length=4000)
    costume_mode: Literal["garment_only", "worn"] | None = None
    voice: str | None = Field(default=None, max_length=2000)
    audio_usage: Literal["voice", "music", "ambience", "sfx", "unclassified"] = "unclassified"
    language: str | None = Field(default=None, max_length=40)
    voice_id: str | None = Field(default=None, max_length=200)
    speech_preset: SpeechBinding | None = None
    location: str | None = Field(default=None, max_length=1000)
    time_of_day: str | None = Field(default=None, max_length=200)
    weather: str | None = Field(default=None, max_length=500)
    lighting: str | None = Field(default=None, max_length=1000)
    atmosphere: str | None = Field(default=None, max_length=2000)
    environment: str | None = Field(default=None, max_length=2000)
    material: str | None = Field(default=None, max_length=1000)
    owner: str | None = Field(default=None, max_length=1000)
    story_function: str | None = Field(default=None, max_length=2000)
    hair: str | None = Field(default=None, max_length=1000)
    makeup: str | None = Field(default=None, max_length=1000)
    injury: str | None = Field(default=None, max_length=1000)
    stage: str | None = Field(default=None, max_length=1000)
    pitch: str | None = Field(default=None, max_length=500)
    texture: str | None = Field(default=None, max_length=1000)
    pace: str | None = Field(default=None, max_length=500)
    accent: str | None = Field(default=None, max_length=500)
    story_state: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_group(self):
        if "character_form" not in self.model_fields_set:
            return self
        if self.character_form == "single" and (self.group_label or self.group_size is not None):
            raise ValueError("单人角色不能设置群体人数或称呼")
        if self.character_form == "fixed_group" and (self.group_size is None or self.group_size > 12):
            raise ValueError("固定小组须设置2至12人的明确人数；更大群体使用背景人群")
        return self


class ProductionSource(StrictContract):
    kind: Literal["manual", "script", "ai_suggestion", "reused"]
    episode_id: int | None = Field(default=None, ge=1)
    scene_id: int | None = Field(default=None, ge=1)
    shot_id: int | None = Field(default=None, ge=1)
    segment_id: int | None = Field(default=None, ge=1)
    canvas_node_key: str | None = Field(default=None, min_length=1, max_length=64)
    script_revision: int | None = Field(default=None, ge=0)
    locator: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid_structure(self):
        if self.script_revision is not None and self.episode_id is None:
            raise ValueError("剧本版本必须同时指定分集")
        if self.scene_id is not None and self.episode_id is None:
            raise ValueError("场景来源必须同时指定分集")
        if self.shot_id is not None and self.scene_id is None:
            raise ValueError("分镜来源必须同时指定场景")
        if self.segment_id is not None and self.episode_id is None:
            raise ValueError("片段来源必须同时指定分集")
        return self


class ProductionSourceOut(ProductionSource):
    status: Literal["valid", "stale", "missing", "legacy"]
    display_path: str
    episode_number: int | None = None
    episode_title: str | None = None
    current_script_revision: int | None = None
    scene_order: int | None = None
    scene_name: str | None = None
    shot_order: int | None = None
    segment_order: int | None = None
    segment_title: str | None = None
    canvas_node_label: str | None = None


class AssetArchiveImpact(StrictContract):
    usage_count: int = Field(ge=0)
    adoption_count: int = Field(ge=0)
    historical_snapshot_count: int = Field(ge=0)
    dependent_asset_count: int = Field(ge=0)


class AssetAdoption(StrictContract):
    # Stable namespace, e.g. appearance:work/angle:front or voice:default.
    key: str = Field(min_length=1, max_length=120, pattern=r"^[\w:/.-]+$")
    version_id: int = Field(ge=1)


class AssetScopeOverride(StrictContract):
    target_type: Literal["scene", "segment"]
    target_id: int = Field(ge=1)
    description: str | None = Field(default=None, max_length=4000)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    profile: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_content(self):
        if self.description is None and self.prompt_anchor is None and not self.profile:
            raise ValueError("局部覆盖缺少修改内容")
        return self


class AssetProductionPatch(StrictContract):
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=128)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    profile: AssetProfile | None = None
    sources: list[ProductionSource] | None = Field(default=None, max_length=100)
    adoptions: list[AssetAdoption] | None = Field(default=None, max_length=100)
    scope_override: AssetScopeOverride | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set.intersection(
            {"prompt_anchor", "profile", "sources", "adoptions", "scope_override", "archived"}
        ):
            raise ValueError("缺少修改内容")
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set - {"request_id", "expected_revision"}
        ):
            raise ValueError("使用空列表清除关系，字段不能传null")
        keys = [item.key for item in self.adoptions or []]
        if len(keys) != len(set(keys)):
            raise ValueError("采用用途不能重复")
        return self


class AdoptedMedia(StrictContract):
    key: str
    version_id: int
    media_file_id: int
    kind: str
    origin: Literal["explicit", "legacy_final"]
    audio_purpose: Literal["voice_reference", "dialogue_audio", "music", "ambience", "sfx", "unclassified"] | None = None


class AssetProductionContext(StrictContract):
    asset_id: int
    project_id: int
    revision: int
    archived: bool
    prompt_anchor: str | None
    profile: AssetProfile
    sources: list[ProductionSourceOut]
    adoptions: list[AdoptedMedia]
    scope_overrides: list[AssetScopeOverride] = Field(default_factory=list)
    effective_scope_keys: list[str] = Field(default_factory=list)
    effective_description: str | None = None
    readiness: Literal["archived", "missing_media", "reference_selected"]
    readiness_reasons: list[str]


class AssetProductionOut(AssetProductionContext):
    archive_impact: AssetArchiveImpact


class SecondsRange(StrictContract):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("结束时间必须大于开始时间")
        return self
