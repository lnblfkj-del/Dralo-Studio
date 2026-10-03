"""Job lease acquisition, recovery and execution-state transitions."""

from datetime import timedelta
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from app.services.job_authorization_service import execution_authorized

from app.core.config import settings
from app.models import (
    JOB_STATUS_DOWNLOADING, JOB_STATUS_PROCESSING, JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING, JOB_STATUS_RUNNING, JOB_STATUS_FAILED,
    JOB_TYPE_IMAGE, JOB_TYPE_TEXT, JOB_TYPE_VIDEO, Job,
    Provider, ProviderModel, VideoSegment, utcnow,
)
from app.services.job_concurrency_service import (
    ACTIVE_LEASE_STATUSES, LOCAL_JOB_TYPES, REMOTE_EXECUTION_PHASES,
)

async def release_due_retries(session: AsyncSession, local_media_only: bool = False) -> int:
    now = utcnow()
    result = await session.execute(
        update(Job)
        .where(Job.status == JOB_STATUS_RETRYING, Job.available_at <= now)
        .where(Job.job_type.in_(LOCAL_JOB_TYPES) if local_media_only else True)
        .values(status=JOB_STATUS_QUEUED, available_at=None)
    )
    return int(result.rowcount or 0)


async def recover_expired_leases(session: AsyncSession, local_media_only: bool = False) -> int:
    now = utcnow()
    jobs = list(
        (
            await session.execute(
                select(Job).where(
                    Job.status.in_([
                        JOB_STATUS_RUNNING,
                        JOB_STATUS_PROCESSING,
                        JOB_STATUS_DOWNLOADING,
                    ]),
                    Job.lease_expires_at < now,
                    Job.job_type.in_(LOCAL_JOB_TYPES) if local_media_only else True,
                ).with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    failed_children: list[int] = []
    for job in jobs:
        submission = (job.payload or {}).get("text_submission") or {}
        if submission.get("status") == "submitted" or submission.get("response_received"):
            from app.services.job_state_result_service import mark_failed
            await mark_failed(session, job.id, job.worker_id, "GENERATION_FAILED",
                              "执行进程中断；已保存响应可重新处理，未收到响应时不会自动重复请求")
            failed_children.append(job.id)
            continue
        job.worker_id = None
        job.lease_expires_at = None
        uncertain_speech = (
            job.job_type == "tts"
            and (job.payload or {}).get("media_submission", {}).get("started")
        )
        if job.execution_phase in REMOTE_EXECUTION_PHASES:
            # A lost polling lease is not a fresh generation attempt.
            job.status = JOB_STATUS_PROCESSING
            job.available_at = now
        elif uncertain_speech:
            job.status = JOB_STATUS_FAILED
            job.error_code = "PROVIDER_OUTCOME_UNKNOWN"
            job.error_message = "配音提交后执行进程中断，请先核对渠道记录；系统不会自动重发"
            job.finished_at = now
            failed_children.append(job.id)
        elif job.attempts >= job.max_attempts:
            job.status = JOB_STATUS_FAILED
            job.error_code = "LEASE_EXPIRED"
            job.error_message = "任务执行进程中断，且已达到最大重试次数"
            job.finished_at = now
            failed_children.append(job.id)
            if job.target_type == "video_segment" and job.target_id is not None:
                segment = await session.get(VideoSegment, job.target_id)
                if segment is not None:
                    segment.status = "failed"
        else:
            job.status = JOB_STATUS_RETRYING
            from app.services.execution_policy_service import job_retry_backoff_seconds
            job.available_at = now + timedelta(seconds=job_retry_backoff_seconds(job))
            job.error_code = "LEASE_EXPIRED"
            job.error_message = "任务执行进程中断，正在自动重试"
    await session.flush()
    from app.services.job_state_result_service import aggregate_parent_job

    for job_id in failed_children:
        await aggregate_parent_job(session, job_id)
    if failed_children:
        from app.services import creation_service, market_research_service

        failed_jobs = list((await session.scalars(
            select(Job).where(Job.id.in_(failed_children))
        )).all())
        for failed_job in failed_jobs:
            await creation_service.mark_creation_job_failed(session, failed_job)
            await market_research_service.mark_failed(session, failed_job)
    return len(jobs)


async def claim_next_job(session: AsyncSession, worker_id: str, local_media_only: bool = False) -> Job | None:
    if session.bind.dialect.name == "postgresql":
        await session.execute(select(func.set_config(
            "lock_timeout", f"{settings.job_admission_lock_timeout_seconds}s", True)))
        # Serialize admission, not execution. Commit immediately after claiming.
        # A separate statement obtains a fresh READ COMMITTED snapshot for counts.
        await session.execute(select(func.pg_advisory_xact_lock(76422001)))
    await release_due_retries(session, local_media_only)
    await recover_expired_leases(session, local_media_only)
    now = utcnow()
    active_global = aliased(Job)
    active_type = aliased(Job)
    active_provider = aliased(Job)
    active_model = aliased(Job)
    active_local = aliased(Job)
    active_workspace = aliased(Job)
    recent_workspace = aliased(Job)
    workspace_active_count = select(func.count(active_workspace.id)).where(
        active_workspace.workspace_id == Job.workspace_id,
        or_(
            and_(active_workspace.status.in_(ACTIVE_LEASE_STATUSES), active_workspace.worker_id.is_not(None), active_workspace.lease_expires_at >= now),
            and_(active_workspace.status == JOB_STATUS_PROCESSING, active_workspace.execution_phase.in_(REMOTE_EXECUTION_PHASES)),
        ),
    ).correlate(Job).scalar_subquery()
    workspace_last_started = select(func.max(recent_workspace.started_at)).where(
        recent_workspace.workspace_id == Job.workspace_id,
    ).correlate(Job).scalar_subquery()
    # Remote poll/download jobs remain active even while no local Worker owns a
    # lease. They count against global/type/provider generation capacity, but a
    # due continuation must be allowed to poll so a full sliding window can drain.
    global_active_count = (
        select(func.count(active_global.id))
        .where(
            or_(
                and_(
                    active_global.status.in_(ACTIVE_LEASE_STATUSES),
                    active_global.worker_id.is_not(None),
                    active_global.lease_expires_at >= now,
                ),
                and_(
                    active_global.status == JOB_STATUS_PROCESSING,
                    active_global.execution_phase.in_(REMOTE_EXECUTION_PHASES),
                ),
            )
        )
        .scalar_subquery()
    )
    type_active_count = (
        select(func.count(active_type.id))
        .where(
            active_type.job_type == Job.job_type,
            or_(
                and_(
                    active_type.status.in_(ACTIVE_LEASE_STATUSES),
                    active_type.worker_id.is_not(None),
                    active_type.lease_expires_at >= now,
                ),
                and_(
                    active_type.status == JOB_STATUS_PROCESSING,
                    active_type.execution_phase.in_(REMOTE_EXECUTION_PHASES),
                ),
            ),
        )
        .correlate(Job)
        .scalar_subquery()
    )
    provider_active_count = (
        select(func.count(active_provider.id))
        .where(
            active_provider.provider_id == Job.provider_id,
            or_(
                and_(
                    active_provider.status.in_(ACTIVE_LEASE_STATUSES),
                    active_provider.worker_id.is_not(None),
                    active_provider.lease_expires_at >= now,
                ),
                and_(
                    active_provider.status == JOB_STATUS_PROCESSING,
                    active_provider.execution_phase.in_(REMOTE_EXECUTION_PHASES),
                ),
            ),
        )
        .correlate(Job)
        .scalar_subquery()
    )
    provider_limit = (
        select(Provider.max_concurrency)
        .where(Provider.id == Job.provider_id)
        .correlate(Job)
        .scalar_subquery()
    )
    candidate_model_id = Job.payload["provider_model_id"].as_integer()
    active_model_id = active_model.payload["provider_model_id"].as_integer()
    model_active_count = (
        select(func.count(active_model.id))
        .where(
            active_model_id == candidate_model_id,
            or_(
                and_(
                    active_model.status.in_(ACTIVE_LEASE_STATUSES),
                    active_model.worker_id.is_not(None),
                    active_model.lease_expires_at >= now,
                ),
                and_(
                    active_model.status == JOB_STATUS_PROCESSING,
                    active_model.execution_phase.in_(REMOTE_EXECUTION_PHASES),
                ),
            ),
        )
        .correlate(Job)
        .scalar_subquery()
    )
    model_limit = (
        select(ProviderModel.effective_concurrency)
        .where(ProviderModel.id == candidate_model_id)
        .correlate(Job)
        .scalar_subquery()
    )
    model_rate_limit_until = (
        select(ProviderModel.rate_limit_until)
        .where(ProviderModel.id == candidate_model_id)
        .correlate(Job)
        .scalar_subquery()
    )
    type_limit = case(
        (Job.job_type.in_([JOB_TYPE_TEXT, "script", "storyboard"]), settings.job_text_concurrency),
        (Job.job_type == JOB_TYPE_IMAGE, settings.job_image_concurrency),
        (Job.job_type == JOB_TYPE_VIDEO, settings.job_video_concurrency),
        (Job.job_type == "tts", settings.job_tts_concurrency),
        (Job.job_type.in_(LOCAL_JOB_TYPES), 1),
        else_=settings.job_default_type_concurrency,
    )
    # Across all Worker processes, at most one CPU media transform holds an active lease.
    processing_busy = select(active_local.id).where(
        active_local.job_type.in_(LOCAL_JOB_TYPES),
        active_local.status.in_(ACTIVE_LEASE_STATUSES),
        active_local.worker_id.is_not(None),
        active_local.lease_expires_at >= now,
    ).exists()
    paused_parent_ids = select(Job.id).where(
        Job.batch_paused_at.is_not(None), Job.deleted_at.is_(None)
    )
    continuation_ready = and_(
        Job.execution_phase.in_(REMOTE_EXECUTION_PHASES),
        Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_PROCESSING]),
        Job.worker_id.is_(None),
        or_(Job.available_at.is_(None), Job.available_at <= now),
    )
    fresh_ready = and_(
        Job.execution_phase == "submit",
        Job.status == JOB_STATUS_QUEUED,
        global_active_count < settings.job_global_concurrency,
        or_(Job.workspace_id.is_(None), workspace_active_count < settings.job_workspace_concurrency),
        type_active_count < type_limit,
        or_(Job.provider_id.is_(None), provider_active_count < provider_limit),
        or_(
            candidate_model_id.is_(None),
            and_(
                model_limit.is_not(None),
                model_active_count < model_limit,
                or_(model_rate_limit_until.is_(None), model_rate_limit_until <= now),
            ),
        ),
        or_(Job.available_at.is_(None), Job.available_at <= now),
    )
    candidate = (
        select(Job.id)
        .where(
            Job.job_type.in_(LOCAL_JOB_TYPES) if local_media_only else True,
            Job.deleted_at.is_(None),
            or_(Job.parent_job_id.is_(None), Job.parent_job_id.not_in(paused_parent_ids)),
            fresh_ready if local_media_only else or_(continuation_ready, fresh_ready),
            or_(Job.job_type.not_in(LOCAL_JOB_TYPES), ~processing_busy),
            or_(Job.target_type.is_(None), Job.target_type != "video_batch"),
        )
        .order_by(
            case((Job.execution_phase.in_(REMOTE_EXECUTION_PHASES), 0), else_=1),
            Job.priority.asc(),
            workspace_active_count.asc(),
            workspace_last_started.asc().nulls_first(),
            Job.id.asc(),
        )
        .limit(1)
        .scalar_subquery()
    )
    claimed_id = await session.scalar(
        update(Job)
        .where(
            Job.id == candidate,
            Job.worker_id.is_(None),
            Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_PROCESSING]),
        )
        .values(
            status=case(
                (Job.execution_phase == "submit", JOB_STATUS_RUNNING),
                else_=JOB_STATUS_PROCESSING,
            ),
            worker_id=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
            available_at=None,
            started_at=case((Job.started_at.is_(None), now), else_=Job.started_at),
            attempts=case(
                (Job.status == JOB_STATUS_QUEUED, Job.attempts + 1),
                else_=Job.attempts,
            ),
            progress=case(
                (Job.execution_phase == "submit", 5), else_=Job.progress
            ),
            error_code=case(
                (Job.status == JOB_STATUS_QUEUED, None), else_=Job.error_code
            ),
            error_message=case(
                (Job.status == JOB_STATUS_QUEUED, None), else_=Job.error_message
            ),
        )
        .returning(Job.id)
    )
    if claimed_id is None:
        return None
    return await session.get(Job, claimed_id, populate_existing=True)


async def mark_processing(session: AsyncSession, job_id: int, worker_id: str) -> bool:
    result = await session.execute(
        update(Job).execution_options(synchronize_session="fetch")
        .where(execution_authorized(), Job.lease_expires_at > utcnow())
        .where(Job.id == job_id, Job.worker_id == worker_id, Job.status == JOB_STATUS_RUNNING)
        .values(status=JOB_STATUS_PROCESSING, progress=20)
    )
    return bool(result.rowcount)


async def mark_downloading(session: AsyncSession, job_id: int, worker_id: str) -> bool:
    result = await session.execute(
        update(Job).execution_options(synchronize_session="fetch").where(
            execution_authorized(), Job.lease_expires_at > utcnow(),
            Job.id == job_id, Job.worker_id == worker_id,
            Job.status.in_([
                JOB_STATUS_RUNNING,
                JOB_STATUS_PROCESSING,
                JOB_STATUS_DOWNLOADING,
            ]),
        ).values(status="downloading", execution_phase="download", progress=90)
    )
    return bool(result.rowcount)


async def defer_remote_job(
    session: AsyncSession,
    job_id: int,
    worker_id: str,
    *,
    progress: int,
    delay_seconds: float,
) -> bool:
    """Persist a remote in-flight task and release its local Worker lease."""
    now = utcnow()
    result = await session.execute(
        update(Job).execution_options(synchronize_session="fetch")
        .where(execution_authorized(), Job.lease_expires_at > now)
        .where(
            Job.id == job_id,
            Job.worker_id == worker_id,
            Job.status.in_([JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING]),
        )
        .values(
            status=JOB_STATUS_PROCESSING,
            execution_phase="poll",
            progress=max(20, min(89, progress)),
            worker_id=None,
            lease_expires_at=None,
            available_at=now + timedelta(seconds=max(1.0, delay_seconds)),
        )
    )
    return bool(result.rowcount)


async def renew_lease(session: AsyncSession, job_id: int, worker_id: str) -> bool:
    now = utcnow()
    result = await session.execute(
        update(Job).execution_options(synchronize_session="fetch")
        .where(execution_authorized())
        .where(
            Job.id == job_id,
            Job.worker_id == worker_id,
            Job.lease_expires_at > now,
            Job.status.in_([JOB_STATUS_RUNNING, JOB_STATUS_PROCESSING, JOB_STATUS_DOWNLOADING]),
        )
        .values(lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds))
    )
    return bool(result.rowcount)
