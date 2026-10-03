"""C2-A typed commands. No HTML, arbitrary tools or provider parameters."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.canvas_director import DirectorState
from app.schemas.production_contract import AssetProfile, AssetScopeOverride


class ProductionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: int = Field(gt=0)
    label: str = Field(default="主视图", min_length=1, max_length=80)


class SpeechPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_model_id: int = Field(gt=0)
    voice: str = Field(min_length=1, max_length=100)


class ProductionScopeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["scene", "segment"]
    target_id: int = Field(gt=0)


class ProductionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    expected_entity_revision: int = Field(default=0, ge=0)
    expected_entity_token: str = Field(min_length=64, max_length=64)
    expected_production_revision: int = Field(default=0, ge=0)
    request_id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex, min_length=8, max_length=128)
    update_scope: Literal["local", "series"]
    local_target: ProductionScopeTarget | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    prompt_anchor: str | None = Field(default=None, max_length=4000)
    profile: AssetProfile | None = None
    views: list[ProductionView] = Field(default_factory=list, max_length=12)
    primary_media_id: int | None = Field(default=None, gt=0)
    voice_media_id: int | None = Field(default=None, gt=0)
    speech_preset: SpeechPreset | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        if self.update_scope == "local" and self.local_target is None:
            raise ValueError("局部修改必须选择场景或片段")
        if self.update_scope == "series" and self.local_target is not None:
            raise ValueError("全剧共享修改不能携带局部目标")
        return self


class ProductionScopeTargetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["scene", "segment"]
    target_id: int
    label: str
    episode_id: int
    has_override: bool = False
    override: AssetScopeOverride | None = None


class ProductionScopeImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: int
    canvas_card_count: int
    usage_count: int
    affected_episode_count: int
    affected_segment_count: int
    local_targets: list[ProductionScopeTargetOut]


class ProductionBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    asset_id: int = Field(gt=0)


class ProductionOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["create", "update", "connect", "generate", "bind_media", "director_update"]
    node_id: str = Field(min_length=1, max_length=64)
    kind: Literal["text", "character", "scene", "costume", "prop", "voice", "image", "video", "audio", "director"] = "text"
    director_state: DirectorState | None = None
    director_revision: int | None = Field(default=None, ge=0)
    title: str = Field(default="", max_length=255)
    content: str = Field(default="", max_length=4000)
    target_id: str | None = Field(default=None, max_length=64)
    provider_model_id: int | None = Field(default=None, gt=0)
    parameters: dict = Field(default_factory=dict)
    media_id: int | None = Field(default=None, gt=0)


class ProductionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["canvas_actions"]
    operations: list[ProductionOperation] = Field(min_length=1, max_length=8)
