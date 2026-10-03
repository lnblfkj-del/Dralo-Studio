"""Failure-state recovery for creation jobs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_STORY_BIBLE,
    CreationArtifact,
    CreationSession,
    Job,
    SESSION_STATUS_BREAKDOWN_REVIEWING,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_CONFIRMED,
    SESSION_STATUS_DRAFT,
    SESSION_STATUS_OUTLINE_CONFIRMED,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
    SESSION_STATUS_SCRIPT_REVIEWING,
)
from app.services.creation_extraction_service import mark_story_extraction_failed
from app.services.creation_session_service import (
    CREATION_ARTIFACT_TYPES,
    JOB_TARGET_CREATIVE_DIRECTION,
    JOB_TARGET_EPISODE_SCRIPT_BATCH,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_OUTLINE_AGENT,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    JOB_TARGET_SCRIPT_IMPORT,
    JOB_TARGET_SCRIPT_STORY_EXTRACTION,
    JOB_TARGET_SCRIPT_STUDY_BATCH,
    append_message,
)


async def mark_creation_job_failed(session: AsyncSession, job: Job) -> None:
    if job.target_type == "outline_continuation":
        # Durable checkpoints are independent of a failed stage; GET derives
        # failure from its job and resume creates only the missing stage.
        return
    if job.target_type == "script_continuity_check":
        from app.services.script_continuity_review_service import mark_check_failed

        await mark_check_failed(session, job)
        return
    if job.target_type == "script_continuity_repair":
        return
    if job.target_type not in CREATION_ARTIFACT_TYPES or job.target_id is None:
        return
    if job.target_type == JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION:
        return
    if job.target_type in {
        JOB_TARGET_EPISODE_SCRIPT_GENERATION,
        JOB_TARGET_EPISODE_SCRIPT_BATCH,
    }:
        parameters = dict((job.payload or {}).get("parameters") or {})
        session_id = (
            job.target_id
            if job.target_type == JOB_TARGET_EPISODE_SCRIPT_BATCH
            else int(parameters.get("session_id") or 0)
        )
        item = await session.get(CreationSession, session_id) if session_id else None
        if item is not None and item.owner_id == job.owner_id:
            item.status = SESSION_STATUS_SCRIPT_REVIEWING
            await session.flush()
        return
    item = await session.get(CreationSession, job.target_id)
    if item is None or item.owner_id != job.owner_id:
        return
    if job.target_type == JOB_TARGET_SCRIPT_STORY_EXTRACTION:
        await mark_story_extraction_failed(session, item, job)
        return
    if job.target_type == JOB_TARGET_SCRIPT_STUDY_BATCH:
        settings = dict(item.settings)
        study = dict(settings.get("script_study") or {})
        parameters = dict(job.payload.get("parameters") or {})
        study.update(
            {
                "status": "failed",
                "failed_batch": int(parameters.get("batch_index", 0)),
                "parent_job_id": job.parent_job_id,
            }
        )
        settings["script_study"] = study
        if parameters.get("optional_extraction") == "episode_outline":
            states = dict(settings.get("optional_extractions") or {})
            states["episode_outline"] = {
                **dict(states.get("episode_outline") or {}),
                "status": "failed",
                "error": "分集大纲归纳失败；正式剧本和制作流程不受影响。",
            }
            settings["optional_extractions"] = states
        item.settings = settings
        await append_message(
            session,
            item.id,
            "assistant",
            "agent_error",
            f"第 {int(parameters.get('batch_index', 0)) + 1} 批研读失败，可重试失败批次；已完成批次和原始剧本均已保留。",
            job_id=job.id,
        )
        await session.flush()
        return
    if job.target_type in {
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    }:
        settings = dict(item.settings)
        progress = dict(settings.get("asset_breakdown") or {})
        if job.target_type == JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH:
            parameters = dict(job.payload.get("parameters") or {})
            if progress.get("status") != "stale":
                failed_scopes = dict(progress.get("failed_scopes") or {})
                scope_key = str(parameters.get("scope_key") or job.id)
                failed_scopes[scope_key] = {
                    "job_id": job.id,
                    "group": parameters.get("requirement_group"),
                    "episode_numbers": list(parameters.get("episode_numbers") or []),
                    "requirement_types": list(parameters.get("requirement_types") or []),
                    "error_code": job.error_code,
                }
                progress.update(
                    {
                        "status": "failed",
                        "completed": False,
                        "failed_batch": int(parameters.get("batch_index", 0)),
                        "parent_job_id": job.parent_job_id,
                        "failed_scopes": failed_scopes,
                    }
                )
            settings["asset_breakdown"] = progress
            item.settings = settings
        await append_message(
            session,
            item.id,
            "assistant",
            "agent_error",
            "剧本资产拆解未完成，请稍后重试；现有资产未被修改。",
            job_id=job.id,
        )
        await session.flush()
        return
    if job.target_type == JOB_TARGET_CREATIVE_DIRECTION:
        settings = dict(item.settings or {})
        workflow = dict(settings.get("creative_workflow") or {})
        request = dict(workflow.get("direction_proposal_request") or {})
        request.update({"status": "failed", "job_id": job.id})
        workflow["direction_proposal_request"] = request
        settings["creative_workflow"] = workflow
        item.settings = settings
        await append_message(
            session,
            item.id,
            "assistant",
            "agent_error",
            "故事方向生成失败；上一版候选（如有）已保留，请重试。",
            job_id=job.id,
        )
        await session.flush()
        return
    if job.target_type in {JOB_TARGET_OUTLINE_AGENT, JOB_TARGET_SCRIPT_IMPORT}:
        message = (
            "AI 研读失败：返回内容未通过结构校验，请重试。原始剧本和已识别分集均已保留。"
            if job.target_type == JOB_TARGET_SCRIPT_IMPORT
            else "修改未完成，请稍后重试。"
        )
        if job.target_type == JOB_TARGET_SCRIPT_IMPORT:
            settings = dict(item.settings)
            settings["script_study"] = {
                "status": "failed",
                "detected_episode_count": int(settings.get("episode_count") or 1),
                "job_id": job.id,
            }
            item.settings = settings
        await append_message(session, item.id, "assistant", "agent_error", message)
        return
    has_draft = await session.scalar(
        select(CreationArtifact.id).where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == job.target_type,
            CreationArtifact.status == ARTIFACT_STATUS_DRAFT,
        )
    )
    if job.target_type == ARTIFACT_TYPE_STORY_BIBLE:
        item.status = SESSION_STATUS_REVIEWING if has_draft is not None else SESSION_STATUS_DRAFT
        settings = dict(item.settings or {})
        workflow = dict(settings.get("creative_workflow") or {})
        if workflow.get("stage") == "generating_story_bible":
            workflow["stage"] = "story_bible_failed"
            workflow["story_bible_job_id"] = job.id
            settings["creative_workflow"] = workflow
            item.settings = settings
    elif job.target_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
        item.status = (
            SESSION_STATUS_OUTLINE_REVIEWING if has_draft is not None else SESSION_STATUS_CONFIRMED
        )
    elif job.target_type == ARTIFACT_TYPE_EPISODE_SCRIPT:
        item.status = (
            SESSION_STATUS_SCRIPT_REVIEWING
            if has_draft is not None
            else SESSION_STATUS_OUTLINE_CONFIRMED
        )
    else:
        item.status = (
            SESSION_STATUS_BREAKDOWN_REVIEWING
            if has_draft is not None
            else SESSION_STATUS_COMPLETED
        )
    await session.flush()
