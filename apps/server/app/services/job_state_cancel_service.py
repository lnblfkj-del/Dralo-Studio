"""Central job cancellation and durable workflow cleanup."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    JOB_STATUS_CANCELLED, Job, SegmentVideoVersion, Shot,
    ShotVideoVersion, VideoSegment, utcnow,
)
from app.services.job_concurrency_service import BATCH_PARENT_TARGETS, TERMINAL_STATUSES
from app.services.job_state_result_service import aggregate_parent_job
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

async def cancel_job(session: AsyncSession, job: Job) -> Job:
    if job.status in TERMINAL_STATUSES:
        raise ConflictError("任务已结束，无法取消")
    changed = await session.execute(update(Job).where(
        Job.id == job.id, Job.status.not_in(TERMINAL_STATUSES),
    ).values(status=JOB_STATUS_CANCELLED, worker_id=None, lease_expires_at=None, finished_at=utcnow()))
    if changed.rowcount != 1:
        raise ConflictError("任务已结束，无法取消")
    job.status = JOB_STATUS_CANCELLED
    if job.target_type == "video_segment":
        job.progress = 100
    job.worker_id = None
    job.lease_expires_at = None
    job.finished_at = utcnow()
    cancelled_ids = [job.id]
    if job.target_type in BATCH_PARENT_TARGETS:
        children = list((await session.scalars(
            select(Job).where(Job.parent_job_id == job.id)
        )).all())
        for child in children:
            if child.status not in TERMINAL_STATUSES:
                cancelled_ids.append(child.id)
                child.status = JOB_STATUS_CANCELLED
                child.worker_id = None
                child.lease_expires_at = None
                child.finished_at = job.finished_at
                if child.target_type == "shot" and child.target_id is not None:
                    shot = await session.get(Shot, child.target_id)
                    if shot is not None and shot.status != SHOT_STATUS_SUPERSEDED:
                        has_final = await session.scalar(
                            select(ShotVideoVersion.id).where(
                                ShotVideoVersion.shot_id == shot.id,
                                ShotVideoVersion.is_final.is_(True),
                            )
                        )
                        shot.status = "ready" if has_final else "pending"
                if child.target_type == "video_segment" and child.target_id is not None:
                    segment = await session.get(VideoSegment, child.target_id)
                    if segment is not None:
                        has_final = await session.scalar(
                            select(SegmentVideoVersion.id).where(
                                SegmentVideoVersion.segment_id == segment.id,
                                SegmentVideoVersion.is_final.is_(True),
                            )
                        )
                        segment.status = "ready" if has_final else "pending"
        job.status = JOB_STATUS_CANCELLED
        job.progress = 100
        job.result = {**(job.result or {}), "cancelled": len(children)}
    elif job.parent_job_id is not None:
        if job.target_type == "shot" and job.target_id is not None:
            shot = await session.get(Shot, job.target_id)
            if shot is not None and shot.status != SHOT_STATUS_SUPERSEDED:
                has_final = await session.scalar(
                    select(ShotVideoVersion.id).where(
                        ShotVideoVersion.shot_id == shot.id,
                        ShotVideoVersion.is_final.is_(True),
                    )
                )
                shot.status = "ready" if has_final else "pending"
        if job.target_type == "video_segment" and job.target_id is not None:
            segment = await session.get(VideoSegment, job.target_id)
            if segment is not None:
                has_final = await session.scalar(
                    select(SegmentVideoVersion.id).where(
                        SegmentVideoVersion.segment_id == segment.id,
                        SegmentVideoVersion.is_final.is_(True),
                    )
                )
                segment.status = "ready" if has_final else "pending"
        await aggregate_parent_job(session, job.id)
    elif job.target_type == "video_segment" and job.target_id is not None:
        segment = await session.get(VideoSegment, job.target_id)
        if segment is not None:
            has_final = await session.scalar(
                select(SegmentVideoVersion.id).where(
                    SegmentVideoVersion.segment_id == segment.id,
                    SegmentVideoVersion.is_final.is_(True),
                )
            )
            segment.status = "ready" if has_final else "pending"
    from app.services import billing_service

    await billing_service.mark_job_outcome_unknown(session, cancelled_ids)
    # A job can own a second, durable workflow state (for example an AI review
    # stored on a project/session). Cancelling only the Job row leaves that
    # state at "running" forever, so terminal cleanup belongs to the central
    # cancellation path rather than to an individual UI.
    from app.services import creation_service, market_research_service

    cancelled_jobs = [job]
    if len(cancelled_ids) > 1:
        cancelled_jobs = list((await session.scalars(
            select(Job).where(Job.id.in_(cancelled_ids))
        )).all())
    for cancelled_job in cancelled_jobs:
        await creation_service.mark_creation_job_failed(session, cancelled_job)
        await market_research_service.mark_failed(session, cancelled_job)
    await session.flush()
    return job
