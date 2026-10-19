"""R1 planning API. Every mutation stays scoped to the authorized episode."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models import Job, utcnow
from app.schemas.episode_planning_requests import (
    PaidRecovery,
    PlanningCreate,
    PlanningPreflight,
    SavedRecovery,
    SegmentOptimization,
)
from app.services import episode_planning_recovery as recovery
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_read import read_result
from app.services.permission_service import allowed

router = APIRouter()
PREFIX = "/{project_id}/episodes/{episode_id}/production/content-plan"


def require(user, permission):
    if not allowed(user, permission):
        raise PermissionDeniedError("无规划或恢复任务的操作权限")


async def scoped_run(session, episode, job_id):
    job = await session.get(Job, job_id)
    if (
        job is None
        or job.deleted_at is not None
        or job.target_type != workflow.TARGET_RUN
        or job.target_id != episode.id
        or job.project_id != episode.project_id
        or job.workspace_id != episode.workspace_id
        or job.owner_id != episode.owner_id
    ):
        raise NotFoundError("本集规划任务不存在")
    return job


@router.get(PREFIX + "/capabilities")
async def capabilities(video_model_id: int, episode: EpisodeDep, session: SessionDep, background_music: bool = False,
                       resolution: str | None = None):
    from app.services.episode_planning_setup import planning_setup
    _, _, setup = await planning_setup(session, episode, video_model_id, background_music, resolution, allow_unselected=True)
    return setup


@router.get(PREFIX)
async def list_runs(episode: EpisodeDep, session: SessionDep):
    jobs = (
        await session.scalars(
            select(Job)
            .where(
                Job.target_type == workflow.TARGET_RUN,
                Job.target_id == episode.id,
                Job.project_id == episode.project_id,
                Job.workspace_id == episode.workspace_id,
                Job.owner_id == episode.owner_id,
                Job.deleted_at.is_(None),
            )
            .order_by(Job.id.desc())
            .limit(30)
        )
    ).all()
    return [
        {
            "job_id": job.id,
            "request_id": job.payload.get("request_id"),
            "status": job.status,
            "progress": job.progress,
            "stage": (job.result or {}).get("stage"),
            "script_revision": job.payload["request_spec"]["script_revision"],
            "saved_to_workbench": bool(job.payload.get("production_activation")),
        }
        for job in jobs
    ]


@router.get(PREFIX + "/input-context")
async def input_context(episode: EpisodeDep, session: SessionDep):
    from app.services.episode_planning_inputs import source_context

    return await source_context(session, episode)


@router.post(PREFIX + "/preflight")
async def input_preflight(
    payload: PlanningPreflight,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
):
    from app.services.episode_planning_inputs import preflight
    from app.services.episode_planning_setup import planning_setup

    require(user, "tasks.generate")
    _, mode, _ = await planning_setup(session, episode, payload.video_model_id, payload.background_music, payload.resolution)
    return await preflight(
        session,
        episode,
        **payload.model_dump(exclude={"resolution"}),
        mode_key=mode.key,
        reference_bindings=(),
    )


@router.post(PREFIX, status_code=201)
async def create_run(
    payload: PlanningCreate, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    require(user, "tasks.generate")
    from app.services.episode_planning_setup import planning_setup
    # Resolve an accepted request before reading potentially changed defaults.
    previous = await session.scalar(select(Job).where(
        Job.target_type == workflow.TARGET_RUN, Job.target_id == episode.id,
        Job.project_id == episode.project_id, Job.deleted_at.is_(None),
        Job.payload["request_id"].as_string() == payload.request_id))
    if previous:
        if previous.payload.get("submission_input") != payload.model_dump(exclude_none=True):
            raise ConflictError("该请求编号已经用于其他规划参数")
        return await read_result(session, episode, previous.id)
    _, mode, _ = await planning_setup(session, episode, payload.video_model_id, payload.background_music, payload.resolution)
    job = await workflow.create_run(
        session,
        episode,
        **payload.model_dump(exclude={"acknowledge_text_charges", "resolution"}),
        mode_key=mode.key,
    )
    job.payload = {**job.payload, "submission_input": payload.model_dump(exclude_none=True)}
    # Audit the actual caller, not the owner whose project a team member edits.
    if "creation_authorization" not in job.payload:
        job.payload = {
            **job.payload,
            "creation_authorization": {
                "actor_id": user.id,
                "acknowledge_text_charges": True,
                "confirmed_at": utcnow().isoformat(),
            },
        }
    # Validate/serialize before committing the paid queue. A response validation
    # error must not report rejection after the request has already been accepted.
    result = await read_result(session, episode, job.id)
    await session.commit()
    return result


@router.get(PREFIX + "/{job_id}/recovery")
async def recovery_preview(
    job_id: int, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    await scoped_run(session, episode, job_id)
    return await recovery.preview(session, job_id, user.id)


@router.post(PREFIX + "/{job_id}/recover-saved")
async def recover_saved(
    job_id: int, payload: SavedRecovery, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    require(user, "tasks.retry")
    await scoped_run(session, episode, job_id)
    result = await recovery.process_saved(session, job_id, user.id, **payload.model_dump())
    await session.commit()
    return result


@router.post(PREFIX + "/{job_id}/continue-paid")
async def continue_paid(
    job_id: int, payload: PaidRecovery, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    require(user, "tasks.retry")
    await scoped_run(session, episode, job_id)
    result = await recovery.continue_paid(session, job_id, user.id, **payload.model_dump())
    await session.commit()
    return result


@router.post(PREFIX + "/{job_id}/optimize-segment")
async def optimize_segment(
    job_id: int, payload: SegmentOptimization, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    from app.services.episode_planning_optimization import optimize_segment as optimize

    require(user, "tasks.generate")
    parent = await scoped_run(session, episode, job_id)
    run = await optimize(session, parent, user.id, **payload.model_dump(exclude={"acknowledge_new_charges"}))
    result = await read_result(session, episode, run.id)
    await session.commit()
    return result


@router.post(PREFIX + "/{job_id}/save-result")
async def save_result(
    job_id: int, payload: SavedRecovery, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    from app.services.episode_planning_activation import activate

    require(user, "tasks.generate")
    parent = await scoped_run(session, episode, job_id)
    baseline = parent.payload.get("production_baseline")
    if baseline is None:
        raise ConflictError("该任务缺少编辑版本基线，不能自动覆盖当前脚本")
    result = await activate(session, parent, user.id, **payload.model_dump(), **baseline)
    await session.commit()
    return result
