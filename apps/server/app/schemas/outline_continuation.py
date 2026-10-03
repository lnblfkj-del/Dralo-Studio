"""Continuation proposals never accept client-supplied formal episode identities."""

from app.core.creation_limits import MAX_EPISODES

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=64)
    mode: Literal["append", "fill", "optimize"] = "append"
    count: int = Field(default=10, ge=1, le=MAX_EPISODES)
    outline_keys: list[str] = Field(default_factory=list, max_length=MAX_EPISODES)
    duration_seconds: int = Field(default=60, ge=1, le=3600)
    direction: str = Field(min_length=1, max_length=4000)
    ending: str = Field(default="", max_length=2000)
    story_ended: bool = False
    fixed_facts: str = Field(default="", max_length=4000)
    optimization_types: list[
        Literal["pacing", "conflict", "detail", "cliffhanger", "concise", "custom"]
    ] = Field(default_factory=list, max_length=6)


class ProposedEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(min_length=1, max_length=255)
    synopsis: str = Field(min_length=1, max_length=20000)
    dramatic_goal: str = Field(min_length=1, max_length=1000)
    cliffhanger: str = Field(default="", max_length=1000)
    characters: list[str] = Field(default_factory=list, max_length=20)


class EvidenceNote(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=20)


class ContinuationPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=6000)
    character_states: list[EvidenceNote] = Field(default_factory=list, max_length=100)
    unresolved_hooks: list[EvidenceNote] = Field(default_factory=list, max_length=100)
    episode_beats: list[EvidenceNote] = Field(min_length=1, max_length=MAX_EPISODES)


class ContinuityIssue(BaseModel):
    category: Literal[
        "timeline",
        "character",
        "causality",
        "repetition",
        "hook",
        "ending",
        "duration_capacity",
        "source_conflict",
    ]
    severity: Literal["warning", "conflict"]
    episodes: list[int] = Field(min_length=1, max_length=MAX_EPISODES)
    explanation: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=20)


class ContinuityReview(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[ContinuityIssue] = Field(default_factory=list, max_length=100)


class ProposalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    action: Literal["resume", "retry_episode", "save", "recheck", "cancel", "apply"]
    number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    episodes: list[ProposedEpisode] | None = Field(default=None, max_length=MAX_EPISODES)
    update_planned_count: bool = False
    acknowledge_issues: bool = False
