"""Validation and state reset for manual job retries."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.video_submission import remote_video_task_id as _remote_video_task_id
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    EpisodeProduction,
    Job,
    Project,
    Provider,
    ProviderModel,
    Shot,
    VideoSegment,
    utcnow,
)
from app.services.job_concurrency_service import BATCH_PARENT_TARGETS, TERMINAL_STATUSES
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED


async def _prepare_video_segment_retry(session: AsyncSession, job: Job) -> None:
    segment = await session.get(VideoSegment, job.target_id)
    if segment is None or segment.plan_id != (job.payload or {}).get("plan_id"):
        raise ConflictError(f"片段视频任务 #{job.id} 的目标已删除或计划已变化，不能重试")
    active = await session.scalar(
        select(Job.id).where(
            Job.target_type == "video_segment",
            Job.target_id == segment.id,
            Job.id != job.id,
            Job.status.not_in(TERMINAL_STATUSES),
        )
    )
    if active is not None:
        raise ConflictError("该片段已有进行中的视频任务")
    submission = dict((job.payload or {}).get("video_submission") or {})
    if not _remote_video_task_id(submission):
        production = await session.scalar(
            select(EpisodeProduction).where(
                EpisodeProduction.episode_id == segment.episode_id
            )
        )
        if production is None or production.active_plan_id != segment.plan_id:
            raise ConflictError("活动片段计划已变化，请重新预检并创建新候选")
        from app.services.asset_production_service import fingerprint
        from app.services.production_snapshot_service import build_script_snapshot_content

        current = await build_script_snapshot_content(session, segment)
        if fingerprint(current) != (job.payload or {}).get("script_fingerprint"):
            raise ConflictError("片段脚本或资产引用已变化，请重新预检并创建新候选")
        job.execution_phase = "submit"
    else:
        submission.update({
            "started_at": utcnow().isoformat(),
            "resume_count": int(submission.get("resume_count") or 0) + 1,
        })
        job.payload = {**(job.payload or {}), "video_submission": submission}
        job.execution_phase = "download" if job.error_code == "DOWNLOAD_FAILED" else "poll"
    segment.status = "generating"
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == segment.episode_id)
    )
    if production is not None:
        production.last_error = None


async def retry_job(session: AsyncSession, job: Job) -> Job:
    from app.services.long_form_workflow import validate_job
    await validate_job(session, job)
    from app.services import job_state_service
    if job.status not in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
        raise ConflictError("仅失败或已取消的任务可以重试")
    if job.job_type == "video":
        from app.core.video_submission import normalize_rejected_video_submission
        job.payload = normalize_rejected_video_submission(job.payload or {})
    if (job.resolution or {}).get("status") == "superseded":
        raise ConflictError(job.retry_block_reason or "任务已由后续成功结果替代")
    if job.retry_block_reason:
        raise ConflictError(job.retry_block_reason)
    if job.job_type == "text" and (job.failure_detail or {}).get("action") == "none":
        raise ConflictError((job.failure_detail or {})["hint"])
    if job.target_type == "edit_project_asr":
        from app.services.edit_project_asr_service import preflight, runtime_status

        if not runtime_status()["ready"]:
            raise ConflictError(runtime_status()["message"])
        await preflight(session, job)
    if job.target_type == "edit_project_export":
        from app.services.edit_project_export_service import prepare_retry

        await prepare_retry(session, job)
    if job.target_type == "shot" and job.target_id is not None:
        shot = await session.get(Shot, job.target_id)
        if shot is None or shot.status == SHOT_STATUS_SUPERSEDED:
            raise ConflictError("历史镜头的视频任务不能重试，请在当前计划重新创建任务")
    if job.target_type == "outline_continuation":
        from app.models import CreationSession
        from app.services import outline_continuation_service as continuation
        item = await session.get(CreationSession, job.target_id)
        if item is None or item.owner_id != job.owner_id:
            raise ConflictError("创作会话不可用")
        proposal = await continuation.get_proposal(session, item, job.payload["parameters"]["proposal_id"])
        if proposal.content["status"] in {"applied", "cancelled"} or proposal.content["job_id"] != job.id:
            raise ConflictError("提案已结束或已继续执行，请返回分集大纲查看最新提案")
        _, snapshot = await continuation.source_snapshot(session, item)
        if continuation.digest(snapshot) != proposal.content["baseline_hash"]:
            raise ConflictError("提案基线已变化，请在分集大纲中重新校验")
        await continuation.lock(session, proposal, proposal.revision)
    if job.target_type == "market_research":
        from app.services import market_research_service
        run = await market_research_service.get_run(session, job.target_id, job.owner_id)
        run.status = "queued"
        run.error_message = None
    if job.target_type in {"episode_script_batch", "episode_script_generation"}:
        from app.services.creation_script_generation_service import (
            validate_episode_script_job_sources,
            validate_episode_script_parent_sources,
        )

        if job.target_type == "episode_script_batch":
            await validate_episode_script_parent_sources(
                session, job, require_active=False
            )
            script_children = list((await session.scalars(
                select(Job).where(
                    Job.parent_job_id == job.id,
                    Job.status.in_({JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}),
                )
            )).all())
            for child in script_children:
                await validate_episode_script_job_sources(
                    session, child, require_parent_active=False
                )
        else:
            await validate_episode_script_job_sources(
                session, job, require_parent_active=False
            )
    if job.target_type in {
        "script_asset_breakdown",
        "script_asset_breakdown_batch",
        "script_asset_breakdown_group",
    }:
        from app.services.creation_breakdown_service import (
            validate_script_asset_breakdown_job_sources,
        )

        await validate_script_asset_breakdown_job_sources(
            session, job, require_parent_active=False
        )
        if job.target_type == "script_asset_breakdown_group":
            breakdown_children = list((await session.scalars(
                select(Job).where(
                    Job.parent_job_id == job.id,
                    Job.status.in_({JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}),
                )
            )).all())
            for child in breakdown_children:
                await validate_script_asset_breakdown_job_sources(
                    session, child, require_parent_active=False
                )
    submission = job.payload.get("video_submission", {})
    if submission.get("started") and not _remote_video_task_id(submission):
        raise ConflictError("视频提交结果不确定，请先在当前模型渠道核对任务/账单；不允许盲目重发")
    if job.target_type in {"canvas_node", "canvas_agent"}:
        from app.services import canvas_generation_service, canvas_service
        project = await session.get(Project, job.project_id)
        await canvas_generation_service.submission_lock(session, project)
        await session.refresh(job)
        if job.status not in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            raise ConflictError("任务已被重试或结束，请刷新状态")
        if job.job_type == "media_process":
            from app.services import canvas_processing_service
            await canvas_processing_service.preflight(session, job, retry=True)
        else:
            await canvas_generation_service.validate_model(session, job.payload["provider_model_id"], "audio" if job.job_type == "tts" else job.job_type, job.payload.get("parameters", {}))
            from app.services import canvas_advanced_service
            await canvas_advanced_service.preflight(session, job)
        if job.target_type == "canvas_agent":
            from app.models import AgentSkill
            from app.services import provider_service
            config = await provider_service.get_ai_settings(session)
            skill = await session.get(AgentSkill, job.payload.get("parameters", {}).get("skill_id"))
            if not config.canvas_agent_enabled or not skill or not skill.enabled:
                raise ConflictError("画布 Agent 或对应 Skill 已停用")
            active = await session.scalar(select(Job.id).where(Job.target_type == "canvas_agent", Job.target_id == job.target_id, Job.status.not_in(TERMINAL_STATUSES)))
            if active:
                raise ConflictError("该会话已有进行中的任务")
        if job.target_type == "canvas_node":
            node = await canvas_service.get_canvas_node(session, project.id, job.payload.get("parameters", {}).get("source_node_key", ""))
            await canvas_service.assert_node_unlocked(session, project, node)
            active = await session.scalar(select(Job.id).where(Job.target_type == "canvas_node", Job.target_id == node.id, Job.status.not_in(TERMINAL_STATUSES)))
            if active:
                raise ConflictError("节点已有进行中的任务")
            node.data = {**node.data, "job_id": job.id, "generation_status": "queued"}
    claimed_retry = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status.in_({JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}),
        )
        .values(status=JOB_STATUS_QUEUED)
    )
    if claimed_retry.rowcount != 1:
        raise ConflictError("任务已被重试或状态已变化，请刷新后再操作")
    await session.refresh(job)
    if job.target_type == "video_segment" and job.target_id is not None:
        await _prepare_video_segment_retry(session, job)
    if job.target_type in BATCH_PARENT_TARGETS:
        batch_result = dict(job.result or {})
        children = list((await session.scalars(
            select(Job).where(Job.parent_job_id == job.id)
        )).all())
        for child in children:
            if child.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
                if child.retry_block_reason:
                    raise ConflictError(
                        f"子任务 #{child.id}：{child.retry_block_reason}"
                    )
                child_submission = child.payload.get("video_submission", {})
                if child_submission.get("started") and not _remote_video_task_id(child_submission):
                    raise ConflictError(
                        f"分镜视频任务 #{child.id} 的提交结果不确定，请先核对渠道账单"
                    )
                if child.target_type == "video_segment" and child.target_id is not None:
                    await _prepare_video_segment_retry(session, child)
                await _refresh_text_model_defaults(session, child)
                child.status = JOB_STATUS_QUEUED
                _reset_text_attempt(child)
                child.progress = 0
                child.attempts = 0
                child.available_at = None
                child.error_code = None
                child.error_message = None
                child.result = None
                child.started_at = None
                child.finished_at = None
                if child.target_type == "shot" and child.target_id is not None:
                    shot = await session.get(Shot, child.target_id)
                    if shot is None or shot.is_locked or shot.status == SHOT_STATUS_SUPERSEDED:
                        raise ConflictError(
                            f"分镜视频任务 #{child.id} 的目标已删除、锁定或成为历史镜头，不能重试"
                        )
                    shot.status = "generating"
                if child.target_type == "asset" and child.target_id is not None:
                    from app.services import asset_service

                    asset = await asset_service.get_asset(
                        session, child.project_id, child.target_id
                    )
                    if asset.project_id != child.project_id:
                        raise ConflictError(
                            f"资产图片任务 #{child.id} 的项目变体已失效，不能重试"
                        )
        if job.target_type == "episode_script_batch" and job.error_code == "NEXT_EPISODE_PREPARATION_FAILED":
            from app.services.creation_script_generation_service import queue_next_episode_script_job
            from app.models import CreationSession
            item = await session.get(CreationSession, job.target_id)
            completed_child = max(
                (child for child in children if child.status == "succeeded"),
                key=lambda child: int(child.payload["parameters"]["batch_position"]),
            )
            await queue_next_episode_script_job(
                session, item, completed_child, (completed_child.result or {}).get("continuity_update") or {},
            )
            completed_child.result = {
                key: value for key, value in (completed_child.result or {}).items()
                if key != "next_episode_preparation_error"
            }
        job.status = JOB_STATUS_PROCESSING
    else:
        job.status = JOB_STATUS_QUEUED
        if job.target_type == "episode_script_generation" and job.parent_job_id is not None:
            parent = await session.get(Job, job.parent_job_id)
            if parent is not None and parent.target_type == "episode_script_batch":
                parent.status = JOB_STATUS_PROCESSING
                parent.started_at = None
                parent.finished_at = None
                parent.error_code = None
                parent.error_message = None
        if job.target_type == "script_asset_breakdown_batch" and job.parent_job_id is not None:
            parent = await session.get(Job, job.parent_job_id)
            if parent is not None and parent.target_type == "script_asset_breakdown_group":
                parent.status = JOB_STATUS_PROCESSING
                parent.finished_at = None
                parent.error_code = None
                parent.error_message = None
        if job.target_type == "episode_export" and job.target_id is not None:
            production = await session.scalar(
                select(EpisodeProduction).where(
                    EpisodeProduction.episode_id == job.target_id
                )
            )
            if production is not None:
                production.last_error = None
    job.progress = 0
    await _refresh_text_model_defaults(session, job)
    _reset_text_attempt(job)
    job.attempts = 0
    job.available_at = None
    job.error_code = None
    job.error_message = None
    job.result = None
    job.started_at = None
    job.finished_at = None
    if job.target_type in BATCH_PARENT_TARGETS:
        job.result = batch_result
        if children:
            await job_state_service.aggregate_parent_job(session, children[0].id)
    if job.target_type in {"episode_script_batch", "episode_script_generation"}:
        from app.models import CreationSession
        from app.services.creation_session_service import SESSION_STATUS_SCRIPT_GENERATING

        parameters = dict((job.payload or {}).get("parameters") or {})
        creation_session_id = (
            job.target_id
            if job.target_type == "episode_script_batch"
            else int(parameters.get("session_id") or 0)
        )
        item = (
            await session.get(CreationSession, creation_session_id)
            if creation_session_id
            else None
        )
        if item is not None and item.owner_id == job.owner_id:
            item.status = SESSION_STATUS_SCRIPT_GENERATING
    await session.flush()
    return job


async def _refresh_text_model_defaults(session: AsyncSession, job: Job) -> None:
    if job.job_type != "text":
        return
    payload = dict(job.payload or {})
    model_id = payload.get("provider_model_id")
    if not model_id or "parameters" not in payload:
        return
    row = await session.execute(
        select(ProviderModel, Provider)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == model_id)
    )
    pair = row.one_or_none()
    if pair is None:
        raise ConflictError("原文本模型已删除，不能重试")
    model, provider = pair
    if not model.enabled or not provider.enabled:
        raise ConflictError("原文本模型或渠道已停用，不能重试")
    from app.services import text_model_policy_service

    snapshot = dict(job.execution_policy_snapshot or {})
    parameters, frozen = text_model_policy_service.resolve(
        model, provider, payload["parameters"], snapshot
    )
    job.payload = {**payload, "parameters": parameters}
    job.execution_policy_snapshot = {**snapshot, "text_model": frozen}


def _reset_text_attempt(job: Job) -> None:
    if job.job_type != "text":
        return
    payload = dict(job.payload or {})
    submission = payload.pop("text_submission", None)
    recovery = payload.pop("response_recovery", None)
    # Keep original responses in JobTextResponse; stale flags must not classify the next call.
    if submission or recovery:
        attempts = list(payload.get("manual_recovery_history") or [])
        attempts.append({"submission": submission, "recovery": recovery, "retried_at": utcnow().isoformat()})
        payload["manual_recovery_history"] = attempts[-20:]
    job.payload = payload
