"""正文、连续性和脚本审核相关 Schema。"""
from app.core.creation_limits import MAX_EPISODES

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.creation_outline import EpisodeOutlineItem


class EpisodeContinuityUpdate(BaseModel):
    start_state: str = Field(default="", max_length=4000)
    end_state: str = Field(default="", max_length=4000)
    character_changes: list[str] = Field(default_factory=list, max_length=100)
    prop_changes: list[str] = Field(default_factory=list, max_length=100)
    resolved_hooks: list[str] = Field(default_factory=list, max_length=100)
    new_hooks: list[str] = Field(default_factory=list, max_length=100)


class EpisodeScriptContent(BaseModel):
    episode_number: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(min_length=1, max_length=255)
    synopsis: str = Field(min_length=1, max_length=4000)
    script: str = Field(min_length=1, max_length=100000)
    continuity_update: EpisodeContinuityUpdate = Field(default_factory=EpisodeContinuityUpdate)


class EpisodeScriptUpdate(BaseModel):
    content: EpisodeScriptContent


class ScriptStudyBatchResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    episodes: list[EpisodeOutlineItem] = Field(min_length=1, max_length=10)


class EpisodeScriptsGenerateRequest(BaseModel):
    episode_numbers: list[int] = Field(default_factory=list, max_length=MAX_EPISODES)
    overwrite: bool = False

    @field_validator("episode_numbers")
    @classmethod
    def unique_episode_numbers(cls, value: list[int]) -> list[int]:
        if any(number < 1 or number > MAX_EPISODES for number in value):
            raise ValueError("集号必须在 1 到 300 之间")
        if len(set(value)) != len(value):
            raise ValueError("集号不能重复")
        return value


class EpisodeScriptOptimizeRequest(BaseModel):
    instruction: str = Field(default="优化节奏、人物动机和对白，保留核心剧情。", max_length=4000)


class EpisodeScriptApplyRequest(BaseModel):
    job_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)


class ScriptContinuityCheckRequest(BaseModel):
    episode_numbers: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("episode_numbers")
    @classmethod
    def valid_unique_numbers(cls, value: list[int]) -> list[int]:
        if any(number < 1 or number > MAX_EPISODES for number in value):
            raise ValueError("集号必须在 1 到 300 之间")
        if len(value) != len(set(value)):
            raise ValueError("集号不能重复")
        return value


class ScriptContinuityEvidence(BaseModel):
    episode_number: int = Field(ge=1, le=MAX_EPISODES)
    source_revision: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=1000)


class ScriptContinuityIssue(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    conflict_type: Literal["character_identity", "character_state", "relationship", "prop_state", "timeline", "location", "unresolved_hook", "causality", "duplicate_event", "other"]
    severity: Literal["warning", "conflict"]
    episodes: list[int] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=1000)
    evidence: list[ScriptContinuityEvidence] = Field(min_length=1, max_length=6)
    suggestion: str = Field(min_length=1, max_length=2000)
    repair_episode_number: int = Field(ge=1, le=MAX_EPISODES)


class ScriptContinuityCheckResult(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[ScriptContinuityIssue] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_issue_ids(self):
        ids = [issue.id for issue in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("一致性问题标识不能重复")
        return self


class ScriptContinuityRepairRequest(BaseModel):
    issue_id: str = Field(min_length=1, max_length=64)
    instruction: str = Field(default="", max_length=2000)


class ScriptContinuityRepairApplyRequest(BaseModel):
    job_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)


class ScriptContinuityIssueDecisionRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)


class ScriptContinuityManualReviewRequest(BaseModel):
    episode_revisions: dict[int, int] = Field(min_length=1, max_length=MAX_EPISODES)
    reason: str = Field(min_length=2, max_length=1000)


class AgentActionApplyRequest(BaseModel):
    selected_option_index: int | None = Field(default=None, ge=0, le=2)
    expected_source_version: int = Field(ge=0)
    selected_character_keys: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("selected_character_keys")
    @classmethod
    def unique_character_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("审核角色不能重复")
        return value


class EpisodeScriptOptimizationResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    title: str = Field(min_length=1, max_length=255)
    synopsis: str = Field(default="", max_length=4000)
    script: str = Field(min_length=1, max_length=100000)
