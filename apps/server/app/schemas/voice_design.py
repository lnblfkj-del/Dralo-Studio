"""Explicit voice design submissions; preview IDs never become speech bindings."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VoiceDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    expected_revision: int = Field(ge=0)
    provider_model_id: int = Field(gt=0)
    description: str = Field(min_length=20, max_length=1000)
    text: str = Field(min_length=100, max_length=1000)
    design_model: Literal["eleven_multilingual_ttv_v2", "eleven_ttv_v3"] = "eleven_multilingual_ttv_v2"
    confirm_cost: Literal[True]


class VoiceDesignAdopt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preview_index: int = Field(ge=0, le=9)
    name: str = Field(min_length=1, max_length=200)
