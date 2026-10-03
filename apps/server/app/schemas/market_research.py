"""短剧市场探查请求与结果结构。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.job import JobOut


class MarketResearchCreate(BaseModel):
    market: Literal["domestic", "overseas"]
    region: str = Field(default="", max_length=64)
    platforms: list[str] = Field(default_factory=list, max_length=8)
    genres: list[str] = Field(default_factory=list, max_length=8)
    audience: str = Field(default="", max_length=128)
    time_range: Literal["7d", "30d", "90d"] = "30d"
    keywords: str = Field(default="", max_length=500)

    @field_validator("platforms", "genres")
    @classmethod
    def normalize_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip()[:40] for item in value if item.strip()]
        return list(dict.fromkeys(normalized))


class MarketTrend(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    signal: str = Field(min_length=1, max_length=1000)
    evidence_source_ids: list[int] = Field(default_factory=list, max_length=12)


class MarketIdea(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1, max_length=1000)
    hook: str = Field(min_length=1, max_length=1000)
    audience: str = Field(min_length=1, max_length=300)
    recommended_format: str = Field(min_length=1, max_length=300)
    why_now: str = Field(min_length=1, max_length=1000)
    evidence_source_ids: list[int] = Field(default_factory=list, max_length=12)


class MarketResearchReport(BaseModel):
    summary: str = Field(min_length=1, max_length=3000)
    trends: list[MarketTrend] = Field(min_length=1, max_length=8)
    ideas: list[MarketIdea] = Field(min_length=1, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=12)


class MarketResearchRunOut(ORMModel):
    id: int
    owner_id: int
    market: str
    region: str
    platforms: list[str]
    genres: list[str]
    audience: str
    time_range: str
    keywords: str
    status: str
    sources: list[dict]
    report: dict | None
    error_message: str | None
    selected_idea_index: int | None
    adopted_project_id: int | None
    adopted_projects: dict[int, int] = Field(default_factory=dict)
    job_id: int | None
    created_at: datetime
    updated_at: datetime


class MarketResearchStartOut(BaseModel):
    run: MarketResearchRunOut
    job: JobOut


class MarketResearchListOut(BaseModel):
    items: list[MarketResearchRunOut]


class MarketResearchDeleteMany(BaseModel):
    run_ids: list[int] = Field(min_length=1, max_length=50)

    @field_validator("run_ids")
    @classmethod
    def normalize_run_ids(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(value))
        if any(run_id < 1 for run_id in normalized):
            raise ValueError("探索记录编号无效")
        return normalized


class MarketResearchDeleteOut(BaseModel):
    deleted_ids: list[int]
