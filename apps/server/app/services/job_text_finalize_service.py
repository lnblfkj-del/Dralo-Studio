"""Shared local finalization for newly generated and preserved text responses."""

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ModelOutputBusinessValidationError
from app.models import Job
from app.services import (
    asset_prompt_service,
    canvas_agent_service,
    creation_service,
    episode_director_pipeline_service,
    episode_director_service,
    h3_prompt_job_service,
    market_research_service,
    text_model_policy_service,
)


async def finalize_text_result(
    session: AsyncSession,
    job: Job,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Apply business validation and writes without contacting a model provider."""
    text_model_policy_service.ensure_complete_result(
        result,
        submission=dict((job.payload or {}).get("text_submission") or {}),
        policy=dict((getattr(job, "execution_policy_snapshot", None) or {}).get("text_model") or {}),
    )
    if job.target_type == asset_prompt_service.TARGET_ASSET_PROMPT_PROPOSAL:
        persisted = await asset_prompt_service.finalize_auto_apply(session, job, result)
    elif job.target_type == h3_prompt_job_service.TARGET_H3_PROMPT_AUTHORING:
        persisted = h3_prompt_job_service.finalize_h3_prompt_job(job, result)
    elif job.target_type == market_research_service.TARGET_MARKET_RESEARCH:
        persisted = await market_research_service.finalize_run(session, job, result)
    elif job.target_type == episode_director_pipeline_service.TARGET_OUTLINE:
        persisted = await episode_director_pipeline_service.finalize_outline(
            session, job, result
        )
    elif job.target_type == episode_director_pipeline_service.TARGET_SEGMENT:
        persisted = await episode_director_pipeline_service.finalize_segment(job, result)
    elif job.target_type == episode_director_service.TARGET_EPISODE_DIRECTOR:
        if (job.payload or {}).get("auto_prepare"):
            from app.services.episode_auto_planning_service import save_draft
            persisted = await save_draft(session, job, result)
        else:
            persisted = episode_director_service.finalize_result(
                job,
                result,
                repair_attempted=bool(result.get("_director_repair_attempted")),
            )
    else:
        await creation_service.finalize_creation_job(session, job, result)
        await canvas_agent_service.finalize_job(session, job, result)
        persisted = result

    if job.target_type != "canvas_text_optimize":
        return persisted
    text = str(persisted.get("text") or "").strip()
    if not text:
        raise ModelOutputBusinessValidationError("优化模型返回空文本，原文已保留")
    original = job.payload.get("parameters", {}).get("optimization_original", "")
    if sorted(re.findall(r"@\{[^}]+\}", text)) != sorted(
        re.findall(r"@\{[^}]+\}", original)
    ):
        raise ModelOutputBusinessValidationError("优化结果改变了节点引用，原文已保留")
    return {**persisted, "optimization_original": original}
