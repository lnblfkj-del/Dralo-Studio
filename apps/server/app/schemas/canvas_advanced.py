"""Closed advanced-image request: capability, source and quote are server-owned."""

from pydantic import BaseModel, ConfigDict, Field


class AdvancedImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=0)
    source_token: str = Field(min_length=64, max_length=64)
    model_token: str = Field(min_length=64, max_length=64)
    tool: str = Field(min_length=1, max_length=40)
    provider_model_id: int = Field(gt=0)
    instructions: str = Field(default="", max_length=2000)
    aspect_ratio: str = "1:1"
    resolution: str = "1k"
    max_cost_cents: int = Field(ge=0, le=100000)
    confirmed: bool = False
