"""Self-contained model output; identity and source offsets belong to the server."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

AssetKind = Literal[
    "character", "costume", "scene", "prop", "character_voice", "music", "ambience", "sound_effect"
]
FactField = Literal[
    "role",
    "character_role",
    "age",
    "gender",
    "appearance",
    "costume",
    "hair",
    "makeup",
    "injury",
    "stage",
    "character_name",
    "location",
    "time",
    "weather",
    "lighting",
    "atmosphere",
    "base_scene_name",
    "variant_label",
    "owner",
    "material",
    "story_function",
    "story_state",
    "pitch",
    "texture",
    "pace",
    "accent",
    "language",
]


class ExtractionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    @field_validator(
        "aliases",
        "facts",
        "scene",
        "performance",
        "timing",
        "linked_visual_key",
        "source_segment_id",
        "source_unit_id",
        "evidence_text",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def normalize_absent_fields(cls, value, info):
        if value is None:
            return [] if info.field_name in {"aliases", "facts"} else ""
        return value


class AssetOccurrence(ExtractionRecord):
    source_segment_id: str = Field(min_length=1, max_length=96)
    source_unit_id: str = Field(default="", max_length=96)
    evidence_text: Annotated[
        str, StringConstraints(strip_whitespace=False, max_length=2000)
    ] = ""
    scene: str = Field(default="", max_length=500)
    performance: str = Field(default="", max_length=2000)
    timing: str = Field(default="", max_length=500)

    @field_validator("evidence_text")
    @classmethod
    def provided_evidence_required(cls, value, info):
        if not value.strip() and not info.data.get("source_unit_id"):
            raise ValueError("evidence must contain source text")
        return value

    @model_validator(mode="after")
    def nonblank_evidence(self):
        if not self.evidence_text.strip() and not self.source_unit_id:
            raise ValueError("evidence must contain source text")
        return self


class AssetFact(ExtractionRecord):
    field: FactField
    value: str = Field(min_length=1, max_length=1200)
    origin: Literal["source", "identity_reference", "proposed"]
    source_segment_id: str = Field(default="", max_length=96)
    source_unit_id: str = Field(default="", max_length=96)
    evidence_text: Annotated[str, StringConstraints(strip_whitespace=False, max_length=2000)] = ""


class ExtractedAsset(ExtractionRecord):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=255)
    aliases: list[Annotated[str, StringConstraints(min_length=1, max_length=255)]] = Field(
        default_factory=list, max_length=20
    )
    description: str = Field(min_length=1, max_length=1800)
    occurrences: list[AssetOccurrence] = Field(min_length=1, max_length=200)
    facts: list[AssetFact] = Field(default_factory=list, max_length=32)
    linked_visual_key: str = Field(default="", max_length=512)


class AssetExtractionResult(ExtractionRecord):
    assets: list[ExtractedAsset] = Field(max_length=1000)
