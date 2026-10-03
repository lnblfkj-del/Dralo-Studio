"""N2 剧集结构规格合同。"""

from app.core.creation_limits import MAX_EPISODES

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NarrativeStructure = Literal["continuous", "independent", "unit", "hybrid"]
NarrativeSpecStatus = Literal["unconfirmed", "confirmed", "needs_review"]
NarrativeSpecSource = Literal["manual", "automatic", "imported", "legacy"]
CharacterReuseStrategy = Literal["fixed", "rotating", "per_episode"]
UnitContinuity = Literal["continuous", "independent", "hybrid"]


class NarrativeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=255)
    episode_start: int = Field(ge=1, le=MAX_EPISODES)
    episode_end: int = Field(ge=1, le=MAX_EPISODES)
    continuity: UnitContinuity = "continuous"
    persistent_facts: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def ordered_range(self) -> "NarrativeUnit":
        if self.episode_end < self.episode_start:
            raise ValueError("单元结束集不能早于开始集")
        return self


class NarrativeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    status: NarrativeSpecStatus = "unconfirmed"
    source: NarrativeSpecSource = "legacy"
    structure: NarrativeStructure | None = None
    character_reuse: CharacterReuseStrategy | None = None
    episode_count: int = Field(default=10, ge=1, le=MAX_EPISODES)
    episode_duration: int = Field(default=90, ge=1, le=3600)
    units: list[NarrativeUnit] = Field(default_factory=list, max_length=MAX_EPISODES)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "NarrativeSpec":
        if self.status == "confirmed" and self.structure is None:
            raise ValueError("已确认的剧集结构必须指定结构类型")
        if self.status == "confirmed" and self.source == "legacy":
            raise ValueError("历史规格不能直接标记为已确认")
        if self.structure != "unit" and self.units:
            raise ValueError("只有单元故事可以填写单元范围")
        if self.status == "confirmed" and self.structure == "unit":
            self._validate_unit_coverage()
        return self

    def _validate_unit_coverage(self) -> None:
        ranges = sorted((item.episode_start, item.episode_end) for item in self.units)
        if not ranges or ranges[0][0] != 1 or ranges[-1][1] != self.episode_count:
            raise ValueError("单元范围必须从第1集覆盖到计划集数")
        previous_end = 0
        for start, end in ranges:
            if start != previous_end + 1:
                raise ValueError("单元范围不能重叠或漏集")
            previous_end = end


def legacy_narrative_spec(*, episode_count: int = 10, episode_duration: int = 90) -> NarrativeSpec:
    return NarrativeSpec(
        episode_count=episode_count,
        episode_duration=episode_duration,
        source="legacy",
        status="unconfirmed",
    )
