"""Agent 与创作方向相关 Schema。"""
import re

from app.core.creation_limits import MAX_EPISODES

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator, field_validator

from app.schemas.creation_outline import EpisodeOutlineContent
from app.schemas.creation_story import StoryBibleContent
from app.schemas.narrative_spec import NarrativeSpec
from app.schemas.story_planning import (
    CharacterBatchCompletionRequest,
    CharacterOutlineCoverageRequest,
    EventTimelineAdjustmentRequest,
    StoryOverviewAdjustmentRequest,
)


class OutlineAgentAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=40000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.search(r"\.(txt|md|docx)$", cleaned, re.IGNORECASE):
            raise ValueError("Agent 附件仅支持 TXT、Markdown 或 DOCX 文件")
        return cleaned

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Agent 附件正文不能为空")
        return cleaned


class OutlineAgentRequest(BaseModel):
    optimize_all: bool = False
    message: str = Field(min_length=1, max_length=4000)
    attachment_chunk_ids: list[int] = Field(default_factory=list, max_length=8)
    attachments: list[OutlineAgentAttachment] = Field(default_factory=list, max_length=3)
    character_batch_completion: CharacterBatchCompletionRequest | None = None
    character_outline_coverage: CharacterOutlineCoverageRequest | None = None
    story_overview_adjustment: StoryOverviewAdjustmentRequest | None = None
    event_timeline_adjustment: EventTimelineAdjustmentRequest | None = None

    @model_validator(mode="after")
    def one_completion_scope(self):
        scopes = (
            self.character_batch_completion,
            self.character_outline_coverage,
            self.story_overview_adjustment,
            self.event_timeline_adjustment,
        )
        if sum(scope is not None for scope in scopes) + int(self.optimize_all) > 1:
            raise ValueError("批量角色补全与大纲角色覆盖修订不能同时执行")
        return self

    @model_validator(mode="after")
    def validate_attachment_budget(self):
        if sum(len(item.content) for item in self.attachments) > 40000:
            raise ValueError("Agent 单轮附件正文总计不能超过 40000 字")
        return self


class CreativeDirectionProposal(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=2, max_length=24)
    spine: str = Field(min_length=20, max_length=500)
    relationships: str = Field(min_length=12, max_length=400)
    difference: str = Field(min_length=12, max_length=300)


class CreativeDirectionUnderstanding(BaseModel):
    genre: str = Field(min_length=1, max_length=80)
    conflict: str = Field(min_length=8, max_length=240)
    characters: str = Field(min_length=4, max_length=1200)
    tone: str = Field(min_length=2, max_length=200)
    audience: str = Field(min_length=2, max_length=120)


class CreativeDirectionResult(BaseModel):
    understanding: CreativeDirectionUnderstanding
    proposals: list[CreativeDirectionProposal] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_distinct_proposals(self):
        def normalized(value: str) -> str:
            return " ".join(value.casefold().split())
        for field in ("id", "title", "difference"):
            values = [normalized(getattr(item, field)) for item in self.proposals]
            if len(set(values)) != 3:
                raise ValueError(f"三个候选方案的 {field} 必须互不相同")
        return self


class CreativeDirectionSubmit(BaseModel):
    selected_option: str = Field(min_length=1, max_length=120)
    extra_requirements: str = Field(default="", max_length=2000)
    proposal: CreativeDirectionProposal | None = None
    proposal_fingerprint: str | None = Field(default=None, min_length=16, max_length=64)


class CreativeStoryConfirm(BaseModel):
    action: Literal["confirm", "adjust"] = "confirm"
    adjustment: str = Field(default="", max_length=2000)


class CreativeSpecsUpdate(BaseModel):
    episode_count: int = Field(ge=1, le=MAX_EPISODES)
    episode_duration: int = Field(ge=1, le=3600)
    market: Literal["domestic", "overseas"]
    narrative_spec: NarrativeSpec | None = None
    expected_narrative_revision: int | None = Field(default=None, ge=0)


class CreativeDirectionProposeRequest(BaseModel):
    genre: str = Field(default="", max_length=80)
    conflict: str = Field(default="", max_length=120)
    characters: str = Field(default="", max_length=120)
    tone: str = Field(default="", max_length=80)


class CreativeDirectionProposeOut(BaseModel):
    proposals: list[CreativeDirectionProposal]
    fingerprint: str


class StoryOverviewOption(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    logline: str = Field(min_length=1, max_length=1000)
    genre: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=300)
    audience: str = Field(min_length=1, max_length=500)
    world: str = Field(min_length=1, max_length=3000)
    themes: list[str] = Field(min_length=1, max_length=20)
    direction: str = Field(default="", max_length=300)
    highlight: str = Field(default="", max_length=500)
    risk: str = Field(default="", max_length=500)
    change_scope: Literal["小", "中", "大"] | None = None


class OutlineAgentResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    story_options: list[StoryOverviewOption] | None = Field(default=None, min_length=3, max_length=3)
    recommended_option_index: int | None = Field(default=None, ge=0, le=2)
    recommendation_reason: str = Field(default="", max_length=600)
    story_bible: StoryBibleContent | None = None
    episode_outline: EpisodeOutlineContent | None = None

    @model_validator(mode="after")
    def one_tool_per_turn(self):
        if self.story_options is not None and (self.story_bible is not None or self.episode_outline is not None):
            raise ValueError("多个方案不能与单个修改提案同时返回")
        if self.story_bible is not None and self.episode_outline is not None:
            raise ValueError("Agent 每次只能修改一个创作阶段")
        return self


class CharacterBatchStoryPatch(BaseModel):
    characters: list[dict[str, Any]] = Field(min_length=1)


class CharacterBatchAgentResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    story_bible: CharacterBatchStoryPatch
    episode_outline: None = None


class UploadedScriptOptimizationResult(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    story_bible: StoryBibleContent | None = None
    episode_outline: EpisodeOutlineContent | None = None

    @model_validator(mode="after")
    def require_optimized_content(self):
        if self.story_bible is None and self.episode_outline is None:
            raise ValueError("上传剧本优化必须返回故事设定或分集大纲")
        return self
