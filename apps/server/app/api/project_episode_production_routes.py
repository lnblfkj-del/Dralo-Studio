"""Episode video production and export routes."""

from typing import Any

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, EpisodeDep, SessionDep
from app.schemas.job import JobOut
from app.schemas.project import (
    EpisodeEngineeringPackagePreflightOut,
    EpisodeEngineeringPackageStartRequest,
    EpisodeEngineeringPackageVersionOut,
    EpisodeExportPreflightOut,
    EpisodeExportStartRequest,
    EpisodeExportVersionOut,
    EpisodeJianyingDraftPreflightOut,
    EpisodeJianyingDraftStartRequest,
    EpisodeJianyingDraftVersionOut,
    EpisodePremiereXmlPreflightOut,
    EpisodePremiereXmlStartRequest,
    EpisodePremiereXmlVersionOut,
    EpisodeProductionPlanOut,
    EpisodeProductionPlanRequest,
    EpisodeProductionStartRequest,
    H3PromptStartRequest,
    H3VideoRequest,
    H3VideoStartRequest,
    SegmentFirstFrameBatchRequest,
    SegmentFirstFramePlanOut,
    SegmentFirstFramePlanRequest,
    SegmentVideoAttemptRequest,
    SegmentVideoAttemptStartRequest,
)
from app.services import (
    engineering_package_service,
    h3_segment_authoring_service,
    h3_video_job_service,
    jianying_draft_service,
    job_service,
    premiere_xml_service,
    segment_first_frame_service,
)

router = APIRouter(tags=["projects"])


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/h3-prompt/start",
    response_model=JobOut, status_code=status.HTTP_201_CREATED,
)
async def start_h3_segment_prompt(
    segment_id: int, payload: H3PromptStartRequest, episode: EpisodeDep,
    session: SessionDep, user: CurrentUser,
) -> JobOut:
    job = await h3_segment_authoring_service.start_h3_authoring(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
        segment_id=segment_id, video_model_id=payload.video_model_id,
        text_model_id=payload.text_model_id, parameters=payload.parameters,
        request_id=payload.request_id, max_cost_cents=payload.max_cost_cents,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/h3-prompt/{job_id}/review",
)
async def review_h3_segment_prompt(
    segment_id: int, job_id: int, episode: EpisodeDep,
    session: SessionDep, user: CurrentUser,
) -> dict[str, Any]:
    review = await h3_segment_authoring_service.review_h3_segment_authoring(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
        segment_id=segment_id, job_id=job_id,
    )
    await session.commit()
    return review


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/h3-video/plan",
)
async def plan_h3_segment_video(
    segment_id: int, payload: H3VideoRequest, episode: EpisodeDep,
    session: SessionDep, user: CurrentUser,
) -> dict[str, Any]:
    return await h3_video_job_service.preflight_h3_segment_video(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
        segment_id=segment_id, authoring_job_id=payload.authoring_job_id,
        video_model_id=payload.video_model_id, parameters=payload.parameters,
    )


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/h3-video/start",
    response_model=JobOut, status_code=status.HTTP_201_CREATED,
)
async def start_h3_segment_video(
    segment_id: int, payload: H3VideoStartRequest, episode: EpisodeDep,
    session: SessionDep, user: CurrentUser,
) -> JobOut:
    job = await h3_video_job_service.start_h3_segment_video(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
        segment_id=segment_id, authoring_job_id=payload.authoring_job_id,
        video_model_id=payload.video_model_id, parameters=payload.parameters,
        request_id=payload.request_id, max_cost_cents=payload.max_cost_cents,
        expected_fingerprint=payload.expected_fingerprint,
        confirm_media_upload=payload.confirm_media_upload,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-first-frames/plan",
    response_model=SegmentFirstFramePlanOut,
)
async def plan_segment_first_frames(
    payload: SegmentFirstFramePlanRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> SegmentFirstFramePlanOut:
    planned = await segment_first_frame_service.plan_batch(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        segment_ids=payload.segment_ids,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        negative_prompt=payload.negative_prompt,
    )
    return SegmentFirstFramePlanOut.model_validate(planned)

@router.post(
    "/{project_id}/episodes/{episode_id}/production/plan",
    response_model=EpisodeProductionPlanOut,
)
async def plan_episode_production(
    payload: EpisodeProductionPlanRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeProductionPlanOut:
    plan = await job_service.build_episode_video_plan(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        regenerate=payload.regenerate,
    )
    return EpisodeProductionPlanOut.model_validate(plan)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/video/plan",
    response_model=EpisodeProductionPlanOut,
)
async def plan_segment_video_attempt(
    segment_id: int,
    payload: SegmentVideoAttemptRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeProductionPlanOut:
    plan = await job_service.build_segment_video_plan(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        segment_id=segment_id,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
    )
    return EpisodeProductionPlanOut.model_validate(plan)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segments/{segment_id}/video/start",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_segment_video_attempt(
    segment_id: int,
    payload: SegmentVideoAttemptStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await job_service.start_segment_video_attempt(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        segment_id=segment_id,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        request_id=payload.request_id,
        max_cost_cents=payload.max_cost_cents,
        expected_plan_id=payload.expected_plan_id,
        expected_plan_revision=payload.expected_plan_revision,
        expected_video_prompt_fingerprint=payload.expected_video_prompt_fingerprint,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/segment-first-frames",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_segment_first_frames(
    payload: SegmentFirstFrameBatchRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await segment_first_frame_service.create_batch(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        segment_ids=payload.segment_ids,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        negative_prompt=payload.negative_prompt,
        request_id=payload.request_id,
        max_cost_cents=payload.max_cost_cents,
        expected_plan_id=payload.expected_plan_id,
        expected_plan_revision=payload.expected_plan_revision,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/start",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_episode_production(
    payload: EpisodeProductionStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await job_service.start_episode_video_batch(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        regenerate=payload.regenerate,
        request_id=payload.request_id,
        max_cost_cents=payload.max_cost_cents,
        expected_plan_id=payload.expected_plan_id,
        expected_plan_revision=payload.expected_plan_revision,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/export",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_episode_export(
    payload: EpisodeExportStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await job_service.create_episode_export_job(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        request_id=payload.request_id,
        expected_snapshot_fingerprint=payload.expected_snapshot_fingerprint,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/export-preflight",
    response_model=EpisodeExportPreflightOut,
)
async def preflight_episode_export(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeExportPreflightOut:
    result = await job_service.preflight_episode_export(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
    )
    return EpisodeExportPreflightOut.model_validate(result)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/exports",
    response_model=list[EpisodeExportVersionOut],
)
async def list_episode_export_versions(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> list[EpisodeExportVersionOut]:
    versions = await job_service.list_episode_export_versions(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
    )
    return [EpisodeExportVersionOut.model_validate(item) for item in versions]


@router.get(
    "/{project_id}/episodes/{episode_id}/production/engineering-package-preflight",
    response_model=EpisodeEngineeringPackagePreflightOut,
)
async def preflight_episode_engineering_package(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodeEngineeringPackagePreflightOut:
    result = await engineering_package_service.preflight(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
    )
    return EpisodeEngineeringPackagePreflightOut.model_validate(result)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/engineering-packages",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_episode_engineering_package(
    payload: EpisodeEngineeringPackageStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await engineering_package_service.create_job(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        request_id=payload.request_id,
        expected_package_fingerprint=payload.expected_package_fingerprint,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/engineering-packages",
    response_model=list[EpisodeEngineeringPackageVersionOut],
)
async def list_episode_engineering_packages(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> list[EpisodeEngineeringPackageVersionOut]:
    versions = await engineering_package_service.list_versions(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
    )
    return [EpisodeEngineeringPackageVersionOut.model_validate(item) for item in versions]


@router.get(
    "/{project_id}/episodes/{episode_id}/production/premiere-xml-preflight",
    response_model=EpisodePremiereXmlPreflightOut,
)
async def preflight_episode_premiere_xml(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> EpisodePremiereXmlPreflightOut:
    result = await premiere_xml_service.preflight(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
    )
    return EpisodePremiereXmlPreflightOut.model_validate(result)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/premiere-xml-packages",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_episode_premiere_xml(
    payload: EpisodePremiereXmlStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await premiere_xml_service.create_job(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
        request_id=payload.request_id,
        expected_package_fingerprint=payload.expected_package_fingerprint,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/premiere-xml-packages",
    response_model=list[EpisodePremiereXmlVersionOut],
)
async def list_episode_premiere_xml_packages(
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> list[EpisodePremiereXmlVersionOut]:
    versions = await premiere_xml_service.list_versions(
        session, user.id, project_id=episode.project_id, episode_id=episode.id,
    )
    return [EpisodePremiereXmlVersionOut.model_validate(item) for item in versions]


@router.get(
    "/{project_id}/episodes/{episode_id}/production/jianying-draft-preflight",
    response_model=EpisodeJianyingDraftPreflightOut,
)
async def preflight_episode_jianying_draft(
    episode: EpisodeDep, session: SessionDep, user: CurrentUser
) -> EpisodeJianyingDraftPreflightOut:
    result = await jianying_draft_service.preflight(
        session, user.id, project_id=episode.project_id, episode_id=episode.id
    )
    return EpisodeJianyingDraftPreflightOut.model_validate(result)


@router.post(
    "/{project_id}/episodes/{episode_id}/production/jianying-draft-packages",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_episode_jianying_draft(
    payload: EpisodeJianyingDraftStartRequest,
    episode: EpisodeDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await jianying_draft_service.create_job(
        session,
        user.id,
        project_id=episode.project_id,
        episode_id=episode.id,
        request_id=payload.request_id,
        expected_package_fingerprint=payload.expected_package_fingerprint,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/jianying-draft-packages",
    response_model=list[EpisodeJianyingDraftVersionOut],
)
async def list_episode_jianying_draft_packages(
    episode: EpisodeDep, session: SessionDep, user: CurrentUser
) -> list[EpisodeJianyingDraftVersionOut]:
    versions = await jianying_draft_service.list_versions(
        session, user.id, project_id=episode.project_id, episode_id=episode.id
    )
    return [EpisodeJianyingDraftVersionOut.model_validate(item) for item in versions]
