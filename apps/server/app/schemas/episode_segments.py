"""Shared manual segment lifecycle contracts, independent of AI planning."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SegmentPlanAdjustRequest(BaseModel):
    operation: Literal["split", "merge", "reorder"]
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]
    segment_ids: list[int] = Field(default_factory=list, max_length=100)
    after_shot_id: int | None = Field(default=None, ge=1)
    ordered_segment_ids: list[int] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def operation_arguments(self):
        if self.operation == "split" and (len(self.segment_ids) != 1 or self.after_shot_id is None):
            raise ValueError("拆分需要一个片段和拆分位置")
        if self.operation == "merge" and len(self.segment_ids) < 2:
            raise ValueError("合并至少需要两个相邻片段")
        if self.operation == "reorder" and not self.ordered_segment_ids:
            raise ValueError("重新编排需要完整片段顺序")
        return self


class SegmentLifecycleRequest(BaseModel):
    operation: Literal["add", "copy", "archive", "restore", "insert_before", "insert_after", "delete"]
    expected_production_revision: int = Field(ge=0)
    confirmed: Literal[True]
    segment_id: int = Field(ge=1)
    shot_ids: list[int] = Field(default_factory=list, max_length=50)
    title: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def operation_arguments(self):
        if self.operation == "add" and not self.shot_ids:
            raise ValueError("新增片段必须选择从原片段移出的分镜")
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("新增片段不能重复选择分镜")
        return self


class SegmentContinuityOut(BaseModel):
    plan_id: int
    status: Literal["passed", "warning", "blocked"]
    issues: list[dict[str, Any]]
