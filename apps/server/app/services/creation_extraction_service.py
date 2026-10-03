# ruff: noqa: RUF001
"""Optional story/outline extraction from the exact finalized script snapshot."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ModelOutputBusinessValidationError
from app.models import (
    ARTIFACT_STATUS_SUPERSEDED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_STORY_BIBLE,
    SESSION_STATUS_OUTLINE_REVIEWING,
    CreationArtifact,
    CreationSession,
    Job,
)
from app.schemas.creation import EpisodeOutlineContent, ScriptStudyBatchResult, StoryBibleContent
from app.services import script_finalization_service
from app.services.creation_agent_service import create_creation_job
from app.services.creation_session_service import (
    JOB_TARGET_SCRIPT_STORY_EXTRACTION,
    SCRIPT_STUDY_BATCH_SIZE,
    append_message,
    create_artifact,
    parse_structured_result,
)
from app.services.script_source_snapshot import build_script_snapshot, script_snapshot_is_current


async def create_script_story_extraction_job(session: AsyncSession, item: CreationSession) -> Job:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    episodes = await script_finalization_service.require_project_scripts_finalized(
        session, item.project_id, item.owner_id
    )
    outline = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE,
            CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    if outline is None:
        raise ConflictError("请先归纳分集大纲，再提取故事设定")
    snapshot = build_script_snapshot(episodes)
    prompt = f"""你是影视项目总编剧。请根据已经定稿的分集剧本归纳故事设定，不得改写剧本事实。
严格输出单个 JSON 对象，不要 Markdown。字段必须为 title、logline、genre、tone、audience、world、themes、characters、event_timeline。
characters 每项必须包含 name、role、goal、conflict、arc；event_timeline 每项必须包含 title、summary、episode_hint。
所有结论只能来自输入的分集规划；不确定内容用保守表述，不得杜撰。
分集规划：{json.dumps(outline.content, ensure_ascii=False)}"""
    job = await create_creation_job(
        session,
        item,
        JOB_TARGET_SCRIPT_STORY_EXTRACTION,
        prompt,
        "故事设定正在从正式剧本提取",
        parameters={**snapshot, "optional_extraction": "story_bible"},
        agent_key="outline",
        execution_surface="story_bible",
    )
    settings = dict(item.settings or {})
    states = dict(settings.get("optional_extractions") or {})
    states["story_bible"] = {"status": "running", "job_id": job.id, **snapshot}
    settings["optional_extractions"] = states
    item.settings = settings
    await append_message(
        session,
        item.id,
        "user",
        "optional_extraction_request",
        "按当前正式剧本提取故事设定。",
        job_id=job.id,
        parameters=snapshot,
    )
    await session.flush()
    return job


async def finalize_script_story_extraction(
    session: AsyncSession, item: CreationSession, job: Job, result: dict[str, Any]
) -> None:
    parameters = dict(job.payload.get("parameters") or {})
    fingerprint = str(parameters.get("input_fingerprint") or "")
    settings = dict(item.settings or {})
    states = dict(settings.get("optional_extractions") or {})
    if item.project_id is None or not await script_snapshot_is_current(
        session, item.project_id, fingerprint
    ):
        states["story_bible"] = {
            **dict(states.get("story_bible") or {}),
            "status": "stale",
            "stale_reason": "正式剧本版本或目标时长已变化，提取结果未写入。",
        }
        settings["optional_extractions"] = states
        item.settings = settings
        result["stale"] = True
        await session.flush()
        return
    content = parse_structured_result(
        str(result.get("text", "")), StoryBibleContent, "正式剧本故事设定提取结果"
    )
    artifact = await create_artifact(
        session,
        item,
        job,
        ARTIFACT_TYPE_STORY_BIBLE,
        content,
        "已从正式剧本生成可选故事设定，不影响正式剧本与制作准备。",
    )
    states["story_bible"] = {
        "status": "completed",
        "job_id": job.id,
        "artifact_id": artifact.id,
        **{
            key: parameters.get(key)
            for key in ("episode_count", "source_script_revisions", "input_fingerprint")
        },
    }
    settings["optional_extractions"] = states
    item.settings = settings
    result["artifact_id"] = artifact.id
    result["input_fingerprint"] = fingerprint
    await session.flush()


async def finalize_script_study_batch(
    session: AsyncSession, item: CreationSession, job: Job, result: dict[str, Any]
) -> None:
    batch = parse_structured_result(
        str(result.get("text", "")), ScriptStudyBatchResult, "分批剧本研读结果"
    )
    parameters = dict(job.payload.get("parameters") or {})
    batch_index = int(parameters.get("batch_index", 0))
    total_batches = int(parameters.get("total_batches", 1))
    expected_numbers = [int(value) for value in parameters.get("episode_numbers", [])]
    actual_numbers = [entry["number"] for entry in batch["episodes"]]
    if actual_numbers != expected_numbers:
        raise ModelOutputBusinessValidationError(
            f"第 {batch_index + 1} 批返回集号 {actual_numbers}，预期为 {expected_numbers}",
            details={
                "actual_episode_numbers": actual_numbers,
                "expected_episode_numbers": expected_numbers,
            },
        )
    settings = dict(item.settings)
    study = dict(settings.get("script_study") or {})
    batches = dict(study.get("batches") or {})
    batches[str(batch_index)] = batch
    completed_batches = len(batches)
    study.update(
        {
            "status": "failed" if study.get("failed_batch") is not None else "running",
            "batch_size": SCRIPT_STUDY_BATCH_SIZE,
            "total_batches": total_batches,
            "completed_batches": completed_batches,
            "batches": batches,
            "parent_job_id": job.parent_job_id,
        }
    )
    settings["script_study"] = study
    item.settings = settings
    if completed_batches != total_batches:
        await session.flush()
        return
    optional = parameters.get("optional_extraction") == "episode_outline"
    fingerprint = str(parameters.get("input_fingerprint") or "")
    if optional and (
        item.project_id is None
        or not await script_snapshot_is_current(session, item.project_id, fingerprint)
    ):
        states = dict(settings.get("optional_extractions") or {})
        states["episode_outline"] = {
            **dict(states.get("episode_outline") or {}),
            "status": "stale",
            "stale_reason": "正式剧本版本或目标时长已变化，归纳结果未写入。",
        }
        settings["optional_extractions"] = states
        item.settings = settings
        result["stale"] = True
        await session.flush()
        return
    combined = {
        "episodes": [
            episode for index in range(total_batches) for episode in batches[str(index)]["episodes"]
        ]
    }
    validated = EpisodeOutlineContent.model_validate(combined).model_dump()
    artifact = await create_artifact(
        session,
        item,
        job,
        ARTIFACT_TYPE_EPISODE_OUTLINE,
        validated,
        "分批研读完成，已生成可编辑的分集规划。",
    )
    final_settings = dict(item.settings)
    final_study = dict(final_settings.get("script_study") or study)
    final_study.update({"status": "completed", "completed_batches": total_batches})
    final_study.pop("failed_batch", None)
    final_settings["script_study"] = final_study
    if optional:
        states = dict(final_settings.get("optional_extractions") or {})
        states["episode_outline"] = {
            "status": "completed",
            "job_id": job.id,
            "artifact_id": artifact.id,
            **{
                key: parameters.get(key)
                for key in ("episode_count", "source_script_revisions", "input_fingerprint")
            },
        }
        final_settings["optional_extractions"] = states
    else:
        item.status = SESSION_STATUS_OUTLINE_REVIEWING
    item.settings = final_settings
    await append_message(
        session,
        item.id,
        "assistant",
        "agent_reply",
        f"AI 已分 {total_batches} 批完成 {len(validated['episodes'])} 集研读。",
        job_id=job.id,
    )
    result["artifact_id"] = artifact.id
    result["input_fingerprint"] = fingerprint or None
    await session.flush()


async def mark_story_extraction_failed(
    session: AsyncSession, item: CreationSession, job: Job
) -> None:
    settings = dict(item.settings or {})
    states = dict(settings.get("optional_extractions") or {})
    states["story_bible"] = {
        **dict(states.get("story_bible") or {}),
        "status": "failed",
        "error": "故事设定提取失败；正式剧本和制作流程不受影响。",
    }
    settings["optional_extractions"] = states
    item.settings = settings
    await append_message(
        session,
        item.id,
        "assistant",
        "agent_error",
        "故事设定提取失败；正式剧本仍可继续资产拆解。",
        job_id=job.id,
    )
    await session.flush()
