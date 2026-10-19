"""Current-page recovery of original upstream tasks, including historical lineage."""

from sqlalchemy import or_, select

from app.core.errors import ConflictError, NotFoundError
from app.core.video_submission import normalize_rejected_video_submission, remote_video_task_id
from app.models import Episode, EpisodeProduction, Job, VideoSegment
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.team_access import owner_scope


def classify(job):
    submission = normalize_rejected_video_submission(job.payload or {}).get("video_submission") or {}
    remote_id = remote_video_task_id(submission)
    terminal = submission.get("terminal_status") in {"failed", "cancelled"}
    if job.status == "succeeded":
        state = "completed"
    elif terminal:
        state = "terminal_failure"
    elif job.status not in TERMINAL_STATUSES:
        state = "active"
    elif submission.get("started") and not remote_id:
        state = "uncertain"
    elif remote_id:
        state = "resumable"
    else:
        state = "not_submitted"
    return state, submission, remote_id


def unresolved_jobs(jobs):
    succeeded_ids = {job.id for job in jobs if job.status == "succeeded"}
    return [job for job in jobs if classify(job)[0] in {"uncertain", "resumable", "active"}
            and not ((job.resolution or {}).get("status") == "superseded"
                     and (job.resolution or {}).get("by_job_id") in succeeded_ids)]


def recovery_job(jobs):
    candidates = unresolved_jobs(jobs)
    return candidates[0] if candidates else (jobs[0] if jobs else None)


async def lineage_jobs(session, segment, project_id, owner_id):
    # One episode-wide read preserves ancestry from historical versions too.
    rows = (await session.execute(select(
        VideoSegment.lineage_key, VideoSegment.parent_lineage_keys,
    ).where(VideoSegment.episode_id == segment.episode_id))).all()
    parents_by_key = {}
    for key, parents in rows:
        parents_by_key.setdefault(key, set()).update(
            parent for parent in (parents or []) if isinstance(parent, str) and parent
        )
    keys = set()
    pending = [segment.lineage_key, *(segment.parent_lineage_keys or [])]
    # Visited keys bound traversal even when historical lineage contains cycles.
    while pending:
        key = pending.pop()
        if not isinstance(key, str) or not key or key in keys:
            continue
        keys.add(key)
        pending.extend(parents_by_key.get(key, ()))
    lineage = VideoSegment.id == segment.id
    if keys:
        lineage = or_(lineage, VideoSegment.lineage_key.in_(keys))
    return list((await session.scalars(select(Job).join(
        VideoSegment, VideoSegment.id == Job.target_id,
    ).where(
        Job.project_id == project_id, owner_scope(Job.owner_id, owner_id),
        Job.job_type == "video", Job.target_type == "video_segment",
        VideoSegment.episode_id == segment.episode_id, lineage,
    ).order_by(Job.id.desc()))).all())


async def current_segment(session, owner_id, project_id, episode_id, segment_id):
    segment = await session.scalar(select(VideoSegment).join(
        Episode, Episode.id == VideoSegment.episode_id,
    ).join(EpisodeProduction, EpisodeProduction.episode_id == Episode.id).where(
        Episode.id == episode_id, Episode.project_id == project_id,
        owner_scope(Episode.owner_id, owner_id), VideoSegment.id == segment_id,
        VideoSegment.plan_id == EpisodeProduction.active_plan_id,
    ))
    if segment is None:
        raise NotFoundError("当前制作稿中不存在该片段")
    return segment


def describe(segment, job):
    state, submission, remote_id = classify(job) if job else ("none", {}, None)
    blocked = bool(job and job.retry_block_reason)
    return {
        "segment_id": segment.id, "job_id": job.id if job else None,
        "source_segment_id": job.target_id if job else None,
        "historical": bool(job and job.target_id != segment.id),
        "job_status": job.status if job else None, "state": state,
        "remote_task_id": remote_id, "remote_status": submission.get("terminal_status"),
        "can_resume": state == "resumable" and not blocked,
        "can_regenerate": state in {"none", "completed", "terminal_failure", "not_submitted"},
        "regenerate_requires_confirmation": True,
        "resume_creates_new_job": False, "resume_submits_generation": False,
        "error_code": job.error_code if job else None,
        "error_message": job.error_message if job else None,
        "block_reason": job.retry_block_reason if blocked else (
            "上游结果尚不确定，请先核对原渠道任务与账单，不能盲目重新生成"
            if state == "uncertain" else None),
    }


async def recovery(session, owner_id, *, project_id, episode_id, segment_id):
    segment = await current_segment(session, owner_id, project_id, episode_id, segment_id)
    jobs = await lineage_jobs(session, segment, project_id, owner_id)
    result = describe(segment, recovery_job(jobs))
    if unresolved_jobs(jobs):
        result["can_regenerate"] = False
        if not result["block_reason"]:
            result["block_reason"] = "请先恢复或核对原渠道视频任务，再确认付费重新生成"
    return result


async def resume(session, owner_id, *, project_id, episode_id, segment_id, job_id):
    from app.services.job_retry_service import retry_job
    from app.services.job_video_batch_batch import _lock_attempt_scope

    await _lock_attempt_scope(session, owner_id, project_id, episode_id)
    segment = await current_segment(session, owner_id, project_id, episode_id, segment_id)
    jobs = await lineage_jobs(session, segment, project_id, owner_id)
    job = recovery_job(jobs)
    if job is None or job.id != job_id:
        raise ConflictError("可恢复任务已变化，请刷新当前片段后重试")
    state, _, remote_id = classify(job)
    if state in {"active", "completed"} and remote_id:
        return describe(segment, job)
    if not describe(segment, job)["can_resume"]:
        raise ConflictError("仅能续跑已有远端编号的原任务；重新生成需要明确确认费用")
    await retry_job(session, job)
    return describe(segment, job)


async def require_resolved_generation(session, segment, project_id, owner_id):
    if unresolved_jobs(await lineage_jobs(session, segment, project_id, owner_id)):
        raise ConflictError("请先恢复或核对原渠道视频任务，不能重复提交付费生成")
