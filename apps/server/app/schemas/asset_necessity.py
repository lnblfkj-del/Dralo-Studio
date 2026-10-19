"""Frozen necessity policy and evidence contract for asset extraction."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.asset_extraction import AssetKind, ExtractedAsset, ExtractionRecord

POLICY_VERSION = "asset-necessity.nc0.v1"
NecessityReason = Literal[
    "stable_identity", "fixed_appearance", "fixed_voice", "plot_critical",
    "reuse_media", "independent_control", "user_required",
]


class AssetNecessityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["asset-necessity.nc0.v1"] = POLICY_VERSION
    ordinary_objects: Literal["script_only"] = "script_only"
    ordinary_sounds: Literal["script_only"] = "script_only"
    ordinary_speech: Literal["source_and_voice_description"] = "source_and_voice_description"
    preserve_source_events: Literal[True] = True
    frequency_alone_is_sufficient: Literal[False] = False
    auto_generate_media: Literal[False] = False
    auto_delete_existing_assets: Literal[False] = False


class StableVoiceNecessityPolicy(AssetNecessityPolicy):
    version: Literal["asset-necessity.nc3.v2"] = "asset-necessity.nc3.v2"
    stable_voice_profiles: Literal["confirmed_text_only"] = "confirmed_text_only"


class PerformerNecessityPolicy(StableVoiceNecessityPolicy):
    version: Literal["asset-necessity.nc3.v3"] = "asset-necessity.nc3.v3"
    visible_performers: Literal["individual_or_group"] = "individual_or_group"


class ProductionEvidencePolicy(PerformerNecessityPolicy):
    version: Literal["asset-necessity.nc3.v4"] = "asset-necessity.nc3.v4"
    evidence_relation: Literal["scene_entity.v1"] = "scene_entity.v1"
    appearance_states: Literal["scoped.v1"] = "scoped.v1"


class IsolatedItemPolicy(ProductionEvidencePolicy):
    version: Literal["asset-necessity.nc3.v5"] = "asset-necessity.nc3.v5"
    item_failures: Literal["retain_valid.v1"] = "retain_valid.v1"


class ScopedCostumePolicy(IsolatedItemPolicy):
    version: Literal["asset-necessity.nc3.v6"] = "asset-necessity.nc3.v6"
    appearance_projection: Literal["scoped_costume.v1"] = "scoped_costume.v1"


class ProductionClosurePolicy(ScopedCostumePolicy):
    version: Literal["asset-necessity.nc3.v7"] = "asset-necessity.nc3.v7"
    evidence_relation: Literal["scene_entity.v2"] = "scene_entity.v2"
    appearance_completion: Literal["stable_identity.v1"] = "stable_identity.v1"


class CompactExtractionPolicy(ProductionClosurePolicy):
    version: Literal["asset-necessity.nc3.v8"] = "asset-necessity.nc3.v8"
    wire_references: Literal["source_ref.v1"] = "source_ref.v1"
    workload_planning: Literal["scene_load.v1"] = "scene_load.v1"
    item_recovery: Literal["targeted.v1"] = "targeted.v1"


class CrossStyleExtractionPolicy(CompactExtractionPolicy):
    version: Literal["asset-necessity.nc3.v9"] = "asset-necessity.nc3.v9"
    visual_form_scope: Literal["identity_variants.v1"] = "identity_variants.v1"


class SourceOnlyCrowdPolicy(CrossStyleExtractionPolicy):
    version: Literal["asset-necessity.nc3.v10"] = "asset-necessity.nc3.v10"
    audio_only_crowds: Literal["script_only.v1"] = "script_only.v1"


class ReusableMaterialPolicy(SourceOnlyCrowdPolicy):
    version: Literal["asset-necessity.nc3.v11"] = "asset-necessity.nc3.v11"
    candidate_review: Literal["retain_valid_defer_invalid.v1"] = "retain_valid_defer_invalid.v1"


class AssetNecessityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    kind: AssetKind
    needs_asset: bool
    reasons: list[NecessityReason] = Field(default_factory=list)
    source_unit_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def reason_matches_decision(self):
        if self.needs_asset != bool(self.reasons):
            raise ValueError("An independent asset needs a positive necessity reason")
        return self


class NecessityEvidence(ExtractionRecord):
    reasons: list[NecessityReason] = Field(min_length=1, max_length=3)
    source_segment_id: str = Field(min_length=1, max_length=96)
    source_unit_id: str = Field(default="", max_length=96)
    evidence_text: Annotated[str, StringConstraints(strip_whitespace=False, max_length=1200)] = ""
    rationale: str = Field(min_length=1, max_length=240)
    user_requirement_id: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def evidence_required(self):
        if not self.evidence_text.strip() and not self.source_unit_id:
            raise ValueError("necessity evidence must contain source text or a frozen unit")
        return self


class NecessaryExtractedAsset(ExtractedAsset):
    necessity: NecessityEvidence


class NecessaryAssetExtractionResult(ExtractionRecord):
    assets: list[NecessaryExtractedAsset] = Field(max_length=1000)


class PerformerEvidence(NecessityEvidence):
    reasons: list[Literal["stable_identity", "fixed_appearance", "fixed_voice", "plot_critical",
                          "reuse_media", "independent_control", "user_required", "visible_performer"]] = Field(min_length=1, max_length=3)


class PerformerExtractedAsset(NecessaryExtractedAsset):
    necessity: PerformerEvidence
    character_form: Literal["single", "fixed_group", "crowd"] | None = None
    group_label: str | None = Field(default=None, min_length=1, max_length=200)
    group_size: int | None = Field(default=None, ge=2, le=100000)


class PerformerExtractionResult(ExtractionRecord):
    assets: list[PerformerExtractedAsset] = Field(max_length=1000)
