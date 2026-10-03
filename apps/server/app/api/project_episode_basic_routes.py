"""Episode basics, production settings, and script continuity routes."""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, EpisodeDep, ProjectDep, SessionDep
from app.schemas.creation import (
    EpisodeScriptsGenerateRequest,
    ScriptContinuityCheckRequest,
    ScriptContinuityIssueDecisionRequest,
    ScriptContinuityManualReviewRequest,
)
from app.schemas.job import JobOut
from app.schemas.project import (
    EpisodeCreate,
    EpisodeDialogueCueListOut,
    EpisodeOut,
    EpisodeProductionOut,
    EpisodeProductionUpdate,
    ProjectEpisodeBatchStartRequest,
)
from app.services import (
    creation_service,
    job_service,
    production_dialogue_service,
    project_service,
)

router = APIRouter(tags=["projects"])


@router.get("/{project_id}/script-continuity", response_model=dict)
async def get_script_continuity_review(
    project: ProjectDep, session: SessionDep
) -> dict:
    review = await creation_service.get_script_continuity_review(session, project)
    await session.commit()
    return review


@router.post(
    "/{project_id}/script-continuity/check",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def check_script_continuity(
    payload: ScriptContinuityCheckRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    job = await creation_service.create_script_continuity_check_job(
        session, project, payload.episode_numbers
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{project_id}/script-continuity/issues/{issue_id}/accept", response_model=dict)
async def accept_script_continuity_issue(
    issue_id: str,
    payload: ScriptContinuityIssueDecisionRequest,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    review = await creation_service.accept_script_continuity_issue(
        session, project, issue_id, payload.reason, user.id
    )
    await session.commit()
    return review


@router.post("/{project_id}/script-continuity/manual-review", response_model=dict)
async def accept_script_continuity_manual_review(
    payload: ScriptContinuityManualReviewRequest,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    review = await creation_service.accept_script_continuity_manual_review(
        session, project, payload.episode_revisions, payload.reason, user.id
    )
    await session.commit()
    return review


@router.get("/{project_id}/episodes", response_model=list[EpisodeOut])
async def list_episodes(project: ProjectDep, session: SessionDep) -> list[EpisodeOut]:
    episodes = await project_service.list_episodes(session, project.id)
    return [EpisodeOut.model_validate(item) for item in episodes]


@router.get(
    "/{project_id}/episode-productions", response_model=list[EpisodeProductionOut]
)
async def list_episode_productions(
    project: ProjectDep, session: SessionDep
) -> list[EpisodeProductionOut]:
    items = await project_service.list_episode_productions(session, project.id)
    return [EpisodeProductionOut.model_validate(item) for item in items]


@router.post(
    "/{project_id}/episode-productions/batch",
    response_model=list[JobOut],
    status_code=status.HTTP_201_CREATED,
)
async def start_project_episode_batches(
    payload: ProjectEpisodeBatchStartRequest,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> list[JobOut]:
    jobs = await job_service.start_project_episode_batches(
        session,
        user.id,
        project_id=project.id,
        episode_ids=payload.episode_ids,
        provider_model_id=payload.provider_model_id,
        parameters=payload.parameters,
        regenerate=payload.regenerate,
        request_id=payload.request_id,
        max_cost_cents=payload.max_cost_cents,
    )
    await session.commit()
    return [JobOut.model_validate(job) for job in jobs]


@router.post(
    "/{project_id}/episodes",
    response_model=EpisodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_episode(
    payload: EpisodeCreate, project: ProjectDep, session: SessionDep
) -> EpisodeOut:
    episode = await project_service.create_episode(
        session, project, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return EpisodeOut.model_validate(episode)


@router.post(
    "/{project_id}/episodes/scripts/generate",
    response_model=list[JobOut],
    status_code=status.HTTP_201_CREATED,
)
async def generate_episode_scripts(
    payload: EpisodeScriptsGenerateRequest,
    project: ProjectDep,
    session: SessionDep,
) -> list[JobOut]:
    item = await creation_service.get_or_create_project_session(session, project)
    jobs = await creation_service.create_episode_script_generation_jobs(
        session, item, payload.episode_numbers, payload.overwrite
    )
    await session.commit()
    return [JobOut.model_validate(job) for job in jobs]


@router.get("/{project_id}/episodes/{episode_id}", response_model=EpisodeOut)
async def get_episode(episode: EpisodeDep) -> EpisodeOut:
    return EpisodeOut.model_validate(episode)


@router.get(
    "/{project_id}/episodes/{episode_id}/production",
    response_model=EpisodeProductionOut,
)
async def get_episode_production(
    episode: EpisodeDep, session: SessionDep
) -> EpisodeProductionOut:
    item = await project_service.get_episode_production(session, episode)
    return EpisodeProductionOut.model_validate(item)


@router.get(
    "/{project_id}/episodes/{episode_id}/production/dialogue-cues",
    response_model=EpisodeDialogueCueListOut,
)
async def get_episode_dialogue_cues(
    episode: EpisodeDep, session: SessionDep
) -> EpisodeDialogueCueListOut:
    item = await production_dialogue_service.preview_dialogue_cues(session, episode)
    return EpisodeDialogueCueListOut.model_validate(item)


@router.patch(
    "/{project_id}/episodes/{episode_id}/production",
    response_model=EpisodeProductionOut,
)
async def update_episode_production(
    payload: EpisodeProductionUpdate,
    episode: EpisodeDep,
    session: SessionDep,
) -> EpisodeProductionOut:
    item = await project_service.update_episode_production(
        session,
        episode,
        settings=payload.settings.model_dump(),
        expected_revision=payload.expected_revision,
    )
    await session.commit()
    return EpisodeProductionOut.model_validate(item)

