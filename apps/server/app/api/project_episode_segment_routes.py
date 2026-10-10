"""Episode segment plans and content-driven planning routes."""

from fastapi import APIRouter, status

from app.api.deps import EpisodeDep, SessionDep
from app.api.project_episode_planning_routes import router as content_planning_router
from app.schemas.episode_segments import (
    SegmentContinuityOut,
    SegmentLifecycleRequest,
    SegmentPlanAdjustRequest,
)
from app.schemas.project import (
    SegmentManualPlanCreate,
    SegmentProductionPlanCreate,
    SegmentProductionPlanOut,
    SegmentVideoVersionOut,
    SegmentVideoVersionSelectRequest,
)
from app.services import segment_plan_service

router = APIRouter(tags=["projects"])

router.include_router(content_planning_router)


@router.get("/{project_id}/episodes/{episode_id}/production/content-plan/{job_id}")
async def get_content_planning_result(
    job_id: int, episode: EpisodeDep, session: SessionDep, include_video_preview: bool = False,
):
    from app.services.episode_planning_read import read_result

    return await read_result(session, episode, job_id, include_video_preview=include_video_preview)


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
    response_model=SegmentContinuityOut,
)
async def check_segment_plan_continuity(
    episode: EpisodeDep,
    session: SessionDep,
) -> SegmentContinuityOut:
    report = await segment_plan_service.continuity_report(session, episode)
    return SegmentContinuityOut.model_validate(report)


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
