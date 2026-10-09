"""Versioned wire responses, independent of persisted director business objects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.episode_director_pipeline import DirectorOutlineOutput

OUTLINE_PROTOCOL = "director.outline.v2"
SEGMENT_PROTOCOL = "director.segment.v2"
ASSEMBLY_RULE_VERSION = 1


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Invalid Unicode text") from exc
        return value


class ProtectedLineOwner(WireModel):
    line: int = Field(ge=1)
    shot_id: int = Field(ge=1)


class OutlineWireOutput(DirectorOutlineOutput):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol_version: Literal["director.outline.v2"]
    protected_line_owners: list[ProtectedLineOwner] = Field(max_length=2000)


class ShotCreativeOutput(WireModel):
    shot_id: int = Field(ge=1)
    shot_size: str = Field(min_length=1, max_length=64)
    camera_angle: str = Field(min_length=1, max_length=64)
    camera_movement: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=4000)
    subject: str = Field(min_length=1, max_length=255)
    expression: str = Field(min_length=1, max_length=1000)
    dialogue_tone: str = Field(max_length=255)

    @field_validator("shot_size", "camera_angle", "camera_movement", "action", "subject", "expression")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Creative content must not be blank")
        return value


class SegmentWireOutput(WireModel):
    protocol_version: Literal["director.segment.v2"]
    segment_key: str = Field(min_length=1, max_length=64)
    shots: list[ShotCreativeOutput] = Field(max_length=20)
    entry_state: str = Field(min_length=1, max_length=4000)
    exit_state: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(max_length=8000)
    continuity_issues: list[str] = Field(max_length=100)

    @field_validator("segment_key", "entry_state", "exit_state")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Required content must not be blank")
        return value

    @field_validator("continuity_issues")
    @classmethod
    def meaningful_notes(cls, values):
        for value in values:
            if not value.strip():
                raise ValueError("Advisory notes must not be blank")
            value.encode("utf-8")
        return values


def response_contract(stage):
    model = {"outline": OutlineWireOutput, "segment": SegmentWireOutput}[stage]
    version = {"outline": OUTLINE_PROTOCOL, "segment": SEGMENT_PROTOCOL}[stage]
    return {"version": version, "assembly_rules": ASSEMBLY_RULE_VERSION,
            "schema": model.model_json_schema()}
