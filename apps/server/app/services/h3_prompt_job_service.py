"""Text-job authoring and explicit review for MiniMax H3 prompts."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.models import JOB_STATUS_SUCCEEDED, Job
from app.services import job_service
from app.services.h3_prompt_authoring import (
    accept_h3_authored_result,
    build_h3_authoring_contract,
)
from app.services.h3_request_draft import build_h3_request_draft

TARGET_H3_PROMPT_AUTHORING = "h3_prompt_authoring"
REVIEW_VERSION = "h3_prompt_review.v1"


def _snapshot(job: Job) -> dict[str, Any]:
    value = (job.payload or {}).get("h3_authoring")
    if job.target_type != TARGET_H3_PROMPT_AUTHORING or not isinstance(value, dict):
        raise ConflictError("不是 H3 提示词改写任务")
    return value


async def create_h3_prompt_job(
    session: AsyncSession, owner_id: int, *, project_id: int,
    provider_model_id: int, segment_id: int, video_model_id: int,
    source_plan_id: int, source_plan_revision: int,
    source_parameters: dict[str, Any], script: dict[str, Any], profile: dict[str, Any],
    input_contract: dict[str, Any], project_style: str = "", voice_guidance: str = "",
) -> Job:
    """Queue a normal, billed text job; never create a video request here."""
    contract = build_h3_authoring_contract(
        script, profile=profile, input_contract=input_contract,
        project_style=project_style, voice_guidance=voice_guidance,
    )
    job = await job_service.create_text_job(
        session, owner_id, provider_model_id=provider_model_id,
        prompt=contract["instruction"], project_id=project_id,
        parameters={},
    )
    job.target_type = TARGET_H3_PROMPT_AUTHORING
    job.target_id = segment_id
    job.payload = {
        **job.payload,
        "h3_authoring": deepcopy({
            "segment_id": segment_id,
            "video_model_id": video_model_id,
            "source_plan_id": source_plan_id,
            "source_plan_revision": source_plan_revision,
            "source_parameters": source_parameters,
            "contract": contract,
            "script": script,
            "profile": profile,
            "input_contract": input_contract,
            "project_style": project_style,
            "voice_guidance": voice_guidance,
        }),
    }
    await session.flush()
    return job


def finalize_h3_prompt_job(job: Job, result: dict[str, Any]) -> dict[str, Any]:
    """Retain bad model output as a reviewable result, without a paid recall."""
    snapshot = _snapshot(job)
    response = result.get("text")
    if not isinstance(response, str):
        response = ""
    accepted = accept_h3_authored_result(
        response, contract=snapshot["contract"], script=snapshot["script"],
        profile=snapshot["profile"], input_contract=snapshot["input_contract"],
        project_style=snapshot["project_style"],
        voice_guidance=snapshot["voice_guidance"],
    )
    return {**result, "h3_authoring_result": accepted}


def _checked_result(
    job: Job, *, script: dict[str, Any], profile: dict[str, Any],
    input_contract: dict[str, Any], project_style: str, voice_guidance: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = _snapshot(job)
    fresh = build_h3_authoring_contract(
        script, profile=profile, input_contract=input_contract,
        project_style=project_style, voice_guidance=voice_guidance,
    )
    if fresh["source_fingerprint"] != snapshot["contract"]["source_fingerprint"]:
        raise ConflictError("H3 提示词来源已变化, 请重新生成改写")
    accepted = (job.result or {}).get("h3_authoring_result")
    if not isinstance(accepted, dict) or accepted.get("status") != "format_valid":
        raise ConflictError("H3 提示词格式未通过, 不能确认")
    prompt = accepted.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ValidationError("H3 提示词结果为空")
    checked = accept_h3_authored_result(
        (job.result or {}).get("text"), contract=fresh, script=script, profile=profile,
        input_contract=input_contract, project_style=project_style,
        voice_guidance=voice_guidance,
    )
    if (checked["status"] != "format_valid" or checked["prompt_sha256"] != accepted.get("prompt_sha256")
            or checked["prompt"] != prompt):
        raise ConflictError("H3 提示词已变化或格式不再有效")
    return fresh, checked


async def review_h3_prompt_job(
    session: AsyncSession, job: Job, *, reviewer_id: int,
    script: dict[str, Any], profile: dict[str, Any], input_contract: dict[str, Any],
    project_style: str = "", voice_guidance: str = "",
) -> dict[str, Any]:
    """Record explicit approval only while the current source still matches."""
    if reviewer_id <= 0 or reviewer_id != job.owner_id:
        raise ConflictError("只有任务所属用户可确认 H3 提示词")
    if job.status != JOB_STATUS_SUCCEEDED:
        raise ConflictError("H3 提示词任务尚未成功完成")
    fresh, checked = _checked_result(
        job, script=script, profile=profile, input_contract=input_contract,
        project_style=project_style, voice_guidance=voice_guidance,
    )
    prompt = checked["prompt"]
    previous = (job.result or {}).get("h3_prompt_review")
    if previous is not None:
        if (not isinstance(previous, dict)
                or previous.get("source_fingerprint") != fresh["source_fingerprint"]
                or previous.get("prompt_sha256") != checked["prompt_sha256"]
                or previous.get("reviewer_id") != reviewer_id):
            raise ConflictError("H3 提示词已有不同的审核记录")
        return previous
    review = {
        "review_version": REVIEW_VERSION,
        "reviewer_id": reviewer_id,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "source_fingerprint": fresh["source_fingerprint"],
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "status": "reviewed",
        "ready_for_submission": False,
    }
    job.result = {**job.result, "h3_prompt_review": review}
    await session.flush()
    return review


def build_reviewed_h3_request_draft(
    job: Job, *, script: dict[str, Any], profile: dict[str, Any],
    input_contract: dict[str, Any], media: dict[int, dict[str, Any]] | None = None,
    project_style: str = "", voice_guidance: str = "",
) -> dict[str, Any]:
    """Bridge an approved text result to an offline V2 request, never to submit."""
    prompt = reviewed_h3_prompt(
        job, script=script, profile=profile, input_contract=input_contract,
        project_style=project_style, voice_guidance=voice_guidance,
    )
    return build_h3_request_draft(
        script, profile=profile, input_contract=input_contract,
        authored_prompt=prompt, media=media,
    )


def reviewed_h3_prompt(
    job: Job, *, script: dict[str, Any], profile: dict[str, Any],
    input_contract: dict[str, Any], project_style: str = "", voice_guidance: str = "",
) -> str:
    """Return only a still-current, explicitly approved prompt."""
    if job.status != JOB_STATUS_SUCCEEDED:
        raise ConflictError("H3 提示词任务尚未成功完成")
    fresh, checked = _checked_result(
        job, script=script, profile=profile, input_contract=input_contract,
        project_style=project_style, voice_guidance=voice_guidance,
    )
    review = (job.result or {}).get("h3_prompt_review")
    if (not isinstance(review, dict) or review.get("review_version") != REVIEW_VERSION
            or review.get("status") != "reviewed"
            or review.get("source_fingerprint") != fresh["source_fingerprint"]
            or review.get("prompt_sha256") != checked["prompt_sha256"]):
        raise ConflictError("H3 提示词尚未完成当前来源的人工审核")
    return checked["prompt"]
