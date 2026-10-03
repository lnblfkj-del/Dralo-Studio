"""Bounded C6-A tools, not an executable language. Output references point backwards only."""

# ruff: noqa: RUF001
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.canvas_production import ProductionOperation


class GraphOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["group", "ungroup", "arrange"]
    node_id: str = Field(min_length=1, max_length=64)
    node_ids: list[str] = Field(default_factory=list, max_length=100)
    layout: Literal["horizontal", "vertical", "grid"] = "grid"
    title: str = Field(default="组合", max_length=255)


class OutputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str
    role: Literal["reference_image", "first_frame", "last_frame"] = "reference_image"


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    command: ProductionOperation | GraphOperation
    media_from_step: str | None = None
    references_from: list[OutputReference] = Field(default_factory=list, max_length=4)


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["canvas_workflow"]
    steps: list[WorkflowStep] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def safe_dependencies(self):
        previous = {}
        for step in self.steps:
            if step.id in previous:
                raise ValueError("步骤编号不能重复")
            refs = [r.step_id for r in step.references_from]
            if step.media_from_step:
                refs.append(step.media_from_step)
                if step.command.operation != "bind_media" or step.command.media_id is not None:
                    raise ValueError("输出绑定只允许 bind_media，不能同时指定素材 ID")
            if step.references_from and step.command.operation != "generate":
                raise ValueError("生成参考只能用于 generate")
            if any(ref not in previous or previous[ref] != "generate" for ref in refs):
                raise ValueError("输出引用必须来自此前的生成步骤，禁止循环或猜测素材")
            if (
                isinstance(step.command, ProductionOperation)
                and step.command.operation == "generate"
            ):
                allowed = {
                    "aspect_ratio",
                    "resolution",
                    "duration",
                    "references",
                    "voice",
                    "speed",
                    "response_format",
                }
                if set(step.command.parameters) - allowed:
                    raise ValueError("生成参数包含未开放字段")
            previous[step.id] = step.command.operation
        return self


class WorkflowControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "resume", "cancel", "retry", "approve_binding", "undo"]
    expected_version: int = Field(ge=1)
