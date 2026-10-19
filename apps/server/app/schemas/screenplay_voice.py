"""Free, explicit interpretations of unchanged screenplay source lines."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    speaker_kind: Literal["character", "group", "device", "narration", "anonymous"]
    location: Literal["on_screen", "off_screen", "subjective", "unspecified"] = "unspecified"


class SourceInterpretationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_script_revision: int = Field(ge=1)
    expected_production_revision: int = Field(ge=0)
    script_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    line_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_line: int = Field(ge=1)
    kind: Literal["device", "narration", "anonymous", "sound_effects", "ambience"]
    location: Literal["on_screen", "off_screen", "subjective", "unspecified"] = "unspecified"
    confirmed: Literal[True]
