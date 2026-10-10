"""Explicit, non-billable changes to administrator-owned planning evidence."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class PlanningCapabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability: dict
    acknowledge_channel_verification: StrictBool = False
