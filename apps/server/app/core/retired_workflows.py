"""Reject retired task targets; never convert them to another paid workflow."""

from app.core.errors import ConflictError

RETIRED_DIRECTOR_TARGETS = frozenset({
    "episode_director_plan", "episode_director_pipeline",
    "episode_director_outline", "episode_director_segment",
})
RETIRED_MESSAGE = "旧片段规划流程已停用，请从整集规划创建新计划；不会转换旧任务或调用模型"


class WorkflowRetiredError(ConflictError):
    code = "WORKFLOW_RETIRED"
    message = RETIRED_MESSAGE


def require_active_workflow(target_type: str | None) -> None:
    if target_type in RETIRED_DIRECTOR_TARGETS:
        raise WorkflowRetiredError()
