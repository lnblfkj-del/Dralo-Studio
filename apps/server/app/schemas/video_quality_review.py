"""Advisory visual review, never an adoption or script validation gate."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: int = Field(gt=0)
    segment_ids: list[int] = Field(min_length=1, max_length=100)
    provider_model_id: int = Field(gt=0)
    depth: Literal["quick", "detail"] = "quick"


class ReviewStart(ReviewRequest):
    confirmation_token: str
    request_id: str = Field(min_length=1, max_length=100)
    accept_unknown_price: Literal[True]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["identity", "count", "costume", "prop", "scene", "action", "artifact"]
    severity: Literal["notice", "obvious"]
    confidence: Literal["certain", "suspected"]
    time_seconds: float = Field(ge=0, allow_inf_nan=False)
    observation: str = Field(min_length=1, max_length=700)
    evidence: str = Field(min_length=1, max_length=700)
    cause: Literal["asset", "binding", "segment", "model", "unknown"]
    suggestion: str = Field(min_length=1, max_length=700)


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    limitations: str = Field(min_length=1, max_length=1000)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_index: int = Field(ge=0, le=7)
    accepted: bool
