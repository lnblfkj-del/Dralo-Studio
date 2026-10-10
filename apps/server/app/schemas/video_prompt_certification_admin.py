"""Explicit administration of channel prompt evidence, never a generation request."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class VideoPromptCertificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    certifications: dict
    acknowledge_channel_verification: StrictBool = False
