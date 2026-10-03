"""Episode segment plan and director routes."""

from fastapi import APIRouter, status
from sqlalchemy import select
from app.models import Job

from app.api.deps import EpisodeDep, SessionDep
from app.schemas.episode_director import (
    DirectorCapabilityOut,
    DirectorContinuityOut,
    EpisodeDirectorApplyRequest,
    EpisodeDirectorPlanRequest,
    SegmentLifecycleRequest,
    SegmentPlanAdjustRequest,
)
from app.schemas.job import JobOut
from app.schemas.project import (
    SegmentManualPlanCreate,
    SegmentProductionPlanCreate,
    SegmentProductionPlanOut,
    SegmentVideoVersionOut,
    SegmentVideoVersionSelectRequest,
)
from app.services import (
    episode_director_pipeline_service,
    episode_director_service,
    segment_plan_service,
)

router = APIRouter(tags=["projects"])


@router.get("/{project_id}/episodes/{episode_id}/production/director/jobs", response_model=list[JobOut])
async def list_episode_director_jobs(episode: EpisodeDep, session: SessionDep):
    jobs = (await session.scalars(select(Job).where(
        Job.project_id == episode.project_id, Job.target_id == episode.id,
        Job.target_type.in_([
            episode_director_service.TARGET_EPISODE_DIRECTOR,
            episode_director_pipeline_service.TARGET_PIPELINE,
        ]),
        Job.deleted_at.is_(None),
    ).order_by(Job.id.desc()).limit(30))).all()
    return [JobOut.model_validate(job) for job in jobs]

@router.get(
    "/{project_id}/episodes/{episode_id}/production/segment-plan",
    response_model=SegmentProductionPlanOut,
)
async def get_segment_production_plan(
    episode: EpisodeDep, session: SessionDep
) -> SegmentProductionPlanOut:
    item = await segment_plan_service.get_active_plan(session, episode)
    return SegmentProductionPlanOut.model_validate(item)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/manual",
    response_model=SegmentProductionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_segment_production_plan(
    payload: SegmentManualPlanCreate,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    item = await segment_plan_service.create_manual_plan(
        session, episode, **payload.model_dump()
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(item)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/import",
    response_model=SegmentProductionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_segment_production_plan(
    payload: SegmentProductionPlanCreate,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    data = payload.model_dump(exclude={"source_type"})
    item = await segment_plan_service.create_plan(
        session, episode, source_type="import", **data
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(item)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/initialize",
    response_model=SegmentProductionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_segment_production_plan(
    episode: EpisodeDep, session: SessionDep
) -> SegmentProductionPlanOut:
    item = await segment_plan_service.initialize_legacy_plan(session, episode)
    await session.commit()
    return SegmentProductionPlanOut.model_validate(item)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan",
    response_model=SegmentProductionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_segment_production_plan(
    payload: SegmentProductionPlanCreate,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    item = await segment_plan_service.create_plan(
        session,
        episode,
        **payload.model_dump(),
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(item)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/director/capabilities",
    response_model=DirectorCapabilityOut,
)
async def get_episode_director_capabilities(
    video_model_id: int,
    episode: EpisodeDep,
    session: SessionDep,
) -> DirectorCapabilityOut:
    item = await episode_director_service.get_video_capabilities(session, video_model_id)
    return DirectorCapabilityOut.model_validate(item)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/director/plan",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_episode_director_plan(
    payload: EpisodeDirectorPlanRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> JobOut:
    job = await episode_director_service.create_plan_job(
        session,
        episode,
        planner_model_id=payload.planner_model_id,
        video_model_id=payload.video_model_id,
        request_id=payload.request_id,
        mode=payload.mode,
        selected_segment_ids=payload.selected_segment_ids,
        parameters=payload.parameters,
        auto_prepare=payload.auto_prepare,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/director/plan/{job_id}/apply",
    response_model=SegmentProductionPlanOut,
)
async def apply_episode_director_plan(
    job_id: int,
    payload: EpisodeDirectorApplyRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    plan = await episode_director_service.apply_proposal(
        session,
        episode,
        job_id,
        expected_production_revision=payload.expected_production_revision,
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(plan)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/director/plan/{job_id}/reject",
    response_model=JobOut,
)
async def reject_episode_director_plan(
    job_id: int,
    episode: EpisodeDep,
    session: SessionDep,
) -> JobOut:
    job = await episode_director_service.reject_proposal(session, episode, job_id)
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/adjust",
    response_model=SegmentProductionPlanOut,
)
async def adjust_segment_production_plan(
    payload: SegmentPlanAdjustRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    plan = await segment_plan_service.adjust_plan(
        session,
        episode,
        operation=payload.operation,
        expected_production_revision=payload.expected_production_revision,
        segment_ids=payload.segment_ids,
        after_shot_id=payload.after_shot_id,
        ordered_segment_ids=payload.ordered_segment_ids,
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(plan)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/lifecycle",
    response_model=SegmentProductionPlanOut,
)
async def change_segment_lifecycle(
    payload: SegmentLifecycleRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentProductionPlanOut:
    plan = await segment_plan_service.change_segment_lifecycle(
        session,
        episode,
        operation=payload.operation,
        expected_production_revision=payload.expected_production_revision,
        segment_id=payload.segment_id,
        shot_ids=payload.shot_ids,
        title=payload.title,
    )
    await session.commit()
    return SegmentProductionPlanOut.model_validate(plan)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-plan/continuity-check",
    response_model=DirectorContinuityOut,
)
async def check_segment_plan_continuity(
    episode: EpisodeDep,
    session: SessionDep,
) -> DirectorContinuityOut:
    report = await segment_plan_service.continuity_report(session, episode)
    return DirectorContinuityOut.model_validate(report)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/video-versions/{version_id}/select",
    response_model=SegmentVideoVersionOut,
)
async def select_segment_video_version(
    segment_id: int,
    version_id: int,
    payload: SegmentVideoVersionSelectRequest,
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentVideoVersionOut:
    version = await segment_plan_service.select_segment_video_version(
        session,
        episode,
        segment_id,
        version_id,
        expected_plan_revision=payload.expected_plan_revision,
        expected_input_fingerprint=payload.expected_input_fingerprint,
    )
    await session.commit()
    plan = await segment_plan_service.get_active_plan(session, episode)
    selected = next(
        item
        for segment in plan["segments"] if segment["id"] == segment_id
        for item in segment["video_versions"] if item["id"] == version.id
    )
    return SegmentVideoVersionOut.model_validate(selected)
