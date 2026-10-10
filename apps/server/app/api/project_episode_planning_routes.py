"""R1 planning API. Every mutation stays scoped to the authorized episode."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import ValidationError as SchemaError
from sqlalchemy import select

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.models import Job, utcnow
from app.models.episode_planning import EpisodePlanningRecord
from app.schemas.episode_planning_requests import (
    ModelSwitch,
    PaidRecovery,
    PlanningCreate,
    PlanningPreflight,
    ProductionActivation,
    SavedRecovery,
    SegmentOptimization,
)
from app.services import episode_planning_recovery as recovery
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_capability import load_capability
from app.services.episode_planning_contract import fingerprint
from app.services.episode_planning_coordinator import assess_model_switch
from app.services.episode_planning_read import read_result
from app.services.episode_planning_storage import read_plan
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
async def capabilities(video_model_id: int, episode: EpisodeDep, session: SessionDep):
    capability = await load_capability(session, video_model_id)
    return capability.model_dump(mode="json")


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
        }
        for job in jobs
    ]


@router.get(PREFIX + "/input-context")
async def input_context(episode: EpisodeDep, session: SessionDep):
    from app.services.episode_planning_inputs import source_context

    return await source_context(session, episode)


@router.get(PREFIX + "/reference-options")
async def reference_options(
    episode: EpisodeDep,
    session: SessionDep,
    kind: Literal["image", "audio", "video"] = "image",
    keyword: str = Query(default="", max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=100),
):
    from app.services.episode_planning_inputs import reference_options as options

    return await options(session, episode, kind=kind, keyword=keyword, offset=offset, limit=limit)


@router.post(PREFIX + "/preflight")
async def input_preflight(
    payload: PlanningPreflight,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
):
    from app.services.episode_planning_inputs import preflight

    require(user, "tasks.generate")
    try:
        bindings = tuple(item.binding() for item in payload.reference_bindings)
    except SchemaError as exc:
        raise ValidationError("参考素材来源范围或首尾帧边界无效") from exc
    return await preflight(
        session,
        episode,
        **payload.model_dump(exclude={"reference_bindings"}),
        reference_bindings=bindings,
    )


@router.post(PREFIX, status_code=201)
async def create_run(
    payload: PlanningCreate, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    require(user, "tasks.generate")
    if payload.reference_bindings and payload.expected_input_fingerprint is None:
        raise ValidationError("请先校验参考素材和来源范围，再确认生成费用")
    try:
        bindings = tuple(item.binding() for item in payload.reference_bindings)
    except SchemaError as exc:
        raise ValidationError("参考素材来源范围或首尾帧边界无效") from exc
    job = await workflow.create_run(
        session,
        episode,
        **payload.model_dump(exclude={"reference_bindings", "acknowledge_text_charges"}),
        reference_bindings=bindings,
    )
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


@router.get(PREFIX + "/{job_id}/model-check")
async def model_check(
    job_id: int, video_model_id: int, mode_key: str, episode: EpisodeDep, session: SessionDep
):
    job = await scoped_run(session, episode, job_id)
    await workflow.validate_context(session, job, check_current_capability=False)
    record_id = job.payload.get("planning_record_id")
    record = await session.get(EpisodePlanningRecord, record_id) if record_id else None
    if record is None:
        raise ConflictError("尚无冻结计划，请等待规划完成后再检查模型切换")
    if (
        record.episode_id != episode.id
        or record.workspace_id != episode.workspace_id
        or record.execution_epoch != job.payload["epoch"]
    ):
        raise ConflictError("冻结计划归属不匹配，不能检查模型切换")
    capability = await load_capability(session, video_model_id)
    if mode_key not in {mode.key for mode in capability.modes}:
        raise ValidationError("所选模型模式不存在")
    issues = assess_model_switch(read_plan(record), capability, mode_key)
    return {
        "job_id": job.id,
        "plan_fingerprint": record.fingerprint,
        "capability_fingerprint": fingerprint(capability),
        "compatible": not issues,
        "issues": [asdict(issue) for issue in issues],
        "requires_replan": bool(issues),
        "model_called": False,
        "video_submission_ready": False,
    }


@router.post(PREFIX + "/{job_id}/switch-model")
async def switch_model(
    job_id: int, payload: ModelSwitch, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    from app.services.episode_planning_switch import switch_model as persist_switch

    require(user, "tasks.generate")
    parent = await scoped_run(session, episode, job_id)
    run = await persist_switch(session, parent, user.id, **payload.model_dump())
    await session.commit()
    return await read_result(session, episode, run.id)


@router.post(PREFIX + "/{job_id}/production-draft")
async def save_production_draft(
    job_id: int, payload: SavedRecovery, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    from app.services.episode_planning_projection import project_draft

    require(user, "tasks.generate")
    parent = await scoped_run(session, episode, job_id)
    result = await project_draft(session, parent, user.id, **payload.model_dump())
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


@router.post(PREFIX + "/{job_id}/activate-production")
async def activate_production(
    job_id: int, payload: ProductionActivation, episode: EpisodeDep, session: SessionDep, user: CurrentUser
):
    from app.services.episode_planning_activation import activate

    require(user, "tasks.generate")
    parent = await scoped_run(session, episode, job_id)
    result = await activate(session, parent, user.id, **payload.model_dump())
    await session.commit()
    return result
