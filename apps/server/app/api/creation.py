from typing import Any

from fastapi import APIRouter, UploadFile, status
from app.services.source_index_service import parse_source_async

from app.api.deps import CurrentUser, SessionDep
from app.api.outline_continuation import router as continuation_router
from app.api.script_imports import router as script_import_router
from app.schemas.creation import (
    CreationArtifactOut,
    CreationSessionCreate,
    CreationSessionOut,
    EpisodeOutlineConfirm,
    EpisodeOutlineGenerateRequest,
    EpisodeOutlineUpdate,
    EpisodeOutlineOperation,
    EpisodeScriptUpdate,
    SceneShotDraftUpdate,
    SceneShotPublishOut,
    StoryBibleUpdate,
    StoryBibleConfirm,
)
from app.schemas.job import JobOut
from app.schemas.project import ProjectOut
from app.services import creation_service, outline_management_service
from app.services.reference_service import MAX_FILE_BYTES, read_reference

router = APIRouter(prefix="/creation", tags=["creation"])
router.include_router(script_import_router)
router.include_router(continuation_router)


@router.get("/sessions/{session_id}/artifacts/{artifact_id}", response_model=CreationArtifactOut)
async def read_artifact(session_id: int, artifact_id: int, session: SessionDep, user: CurrentUser):
    from app.models import CreationArtifact
    from app.core.errors import NotFoundError
    await creation_service.get_session(session, session_id, user.id)
    artifact = await session.get(CreationArtifact, artifact_id)
    if artifact is None or artifact.session_id != session_id or artifact.artifact_type not in ("story_bible", "episode_outline"):
        raise NotFoundError("版本不存在")
    return CreationArtifactOut.model_validate(artifact)


@router.get("/sessions/{session_id}/artifacts/{artifact_id}/episode-outline/management", response_model=CreationArtifactOut)
async def read_outline_management(session_id: int, artifact_id: int, session: SessionDep, user: CurrentUser):
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await outline_management_service.get_outline(session, item, artifact_id)
    result = CreationArtifactOut.model_validate(artifact)
    result.content = await outline_management_service.normalize(session, item, artifact)
    result.content["deletion_impacts"] = await outline_management_service.deletion_impacts(session, item, result.content)
    from app.services.outline_workflow_service import impact
    result.content["confirmation_impact"] = await impact(session, item, result.content)
    return result


@router.post("/sessions/{session_id}/artifacts/{artifact_id}/episode-outline/operations", response_model=CreationArtifactOut)
async def operate_outline(session_id: int, artifact_id: int, payload: EpisodeOutlineOperation, session: SessionDep, user: CurrentUser):
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await outline_management_service.apply_operation(session, item, artifact_id, payload)
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post("/reference")
async def parse_reference(file: UploadFile, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    try:
        data = await file.read(MAX_FILE_BYTES + 1)
        result = await parse_source_async(read_reference, file.filename or "", data)
        from app.core.config import settings
        if settings.runtime_execution_location == "cloud":
            from app.services.reference_parse_service import store_original
            media = await store_original(session, user.id, file.filename or "", data)
            await session.commit()
            result["source_media_id"] = media.id
        return result
    finally:
        await file.close()


@router.post("/reference-jobs", response_model=JobOut, status_code=201)
async def queue_reference(file: UploadFile, session: SessionDep, user: CurrentUser):
    from app.services.reference_parse_service import submit
    try:
        job = await submit(session, user.id, file.filename or "", await file.read(MAX_FILE_BYTES + 1))
        await session.commit()
        return JobOut.model_validate(job)
    finally:
        await file.close()


@router.get("/reference-jobs/{job_id}/result")
async def reference_result(job_id: int, session: SessionDep, user: CurrentUser):
    from app.services import job_service
    from app.services.reference_parse_service import result
    job = await job_service.get_job(session, job_id, user.id)
    return await result(job)


@router.post("/sessions", response_model=CreationSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreationSessionCreate, session: SessionDep, user: CurrentUser
) -> CreationSessionOut:
    item = await creation_service.create_session(session, user.id, **payload.model_dump())
    await session.commit()
    return CreationSessionOut.model_validate(await creation_service.to_session_out(session, item))


@router.get("/sessions/{session_id}", response_model=CreationSessionOut)
async def get_session(
    session_id: int, session: SessionDep, user: CurrentUser
) -> CreationSessionOut:
    item = await creation_service.get_session(session, session_id, user.id)
    return CreationSessionOut.model_validate(await creation_service.to_session_out(session, item))


@router.post("/sessions/{session_id}/recover", response_model=ProjectOut)
async def recover_legacy_session(
    session_id: int, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    item = await creation_service.get_session(session, session_id, user.id)
    project = await creation_service.recover_legacy_creation_session(session, item, user)
    await session.commit()
    return ProjectOut.model_validate(project)


@router.post(
    "/sessions/{session_id}/story-bible", response_model=JobOut, status_code=status.HTTP_201_CREATED
)
async def generate_story_bible(session_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    item = await creation_service.get_session(session, session_id, user.id)
    job = await creation_service.create_story_bible_job(session, item)
    await session.commit()
    return JobOut.model_validate(job)


@router.patch("/sessions/{session_id}/artifacts/{artifact_id}", response_model=CreationArtifactOut)
async def update_artifact(
    session_id: int,
    artifact_id: int,
    payload: StoryBibleUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.update_story_bible(
        session, item, artifact_id, payload.content.model_dump()
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


from app.schemas.creation import StoryBibleVersionSave


@router.post("/sessions/{session_id}/artifacts/{artifact_id}/story-bible/version", response_model=CreationArtifactOut, status_code=201)
async def save_story_version(session_id: int, artifact_id: int, payload: StoryBibleVersionSave, session: SessionDep, user: CurrentUser) -> CreationArtifactOut:
    from app.services.creation_story_service import save_story_bible_version
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await save_story_bible_version(session, item, artifact_id, payload.content.model_dump(), payload.expected_revision)
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/artifacts/{artifact_id}/confirm", response_model=CreationArtifactOut
)
async def confirm_artifact(
    session_id: int, artifact_id: int, payload: StoryBibleConfirm, session: SessionDep, user: CurrentUser
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.confirm_story_bible(
        session, item, artifact_id, payload.expected_revision
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/episode-outline",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_episode_outline(
    session_id: int,
    session: SessionDep,
    user: CurrentUser,
    payload: EpisodeOutlineGenerateRequest | None = None,
) -> JobOut:
    item = await creation_service.get_session(session, session_id, user.id)
    job = await creation_service.create_episode_outline_job(
        session,
        item,
        expected_story_artifact_id=(payload.expected_story_artifact_id if payload else None),
        expected_story_revision=(payload.expected_story_revision if payload else None),
        confirm_story=payload.confirm_story if payload else False,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.patch(
    "/sessions/{session_id}/artifacts/{artifact_id}/episode-outline",
    response_model=CreationArtifactOut,
)
async def update_episode_outline(
    session_id: int,
    artifact_id: int,
    payload: EpisodeOutlineUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.update_creation_artifact(
        session,
        item,
        artifact_id,
        "episode_outline",
        payload.content.model_dump(),
        payload.expected_revision,
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/artifacts/{artifact_id}/episode-outline/version",
    response_model=CreationArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
async def save_episode_outline_version(
    session_id: int,
    artifact_id: int,
    payload: EpisodeOutlineUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.save_episode_outline_version(
        session, item, artifact_id, payload.content.model_dump(), payload.expected_revision
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/artifacts/{artifact_id}/confirm-outline",
    response_model=CreationArtifactOut,
)
async def confirm_episode_outline(
    session_id: int,
    artifact_id: int,
    session: SessionDep,
    user: CurrentUser,
    payload: EpisodeOutlineConfirm | None = None,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.confirm_episode_outline(
        session,
        item,
        artifact_id,
        payload.content.model_dump() if payload and payload.content else None,
        payload.expected_revision if payload else None,
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/episode-script",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_episode_script(
    session_id: int, session: SessionDep, user: CurrentUser
) -> JobOut:
    item = await creation_service.get_session(session, session_id, user.id)
    job = await creation_service.create_episode_script_job(session, item)
    await session.commit()
    return JobOut.model_validate(job)


@router.patch(
    "/sessions/{session_id}/artifacts/{artifact_id}/episode-script",
    response_model=CreationArtifactOut,
)
async def update_episode_script(
    session_id: int,
    artifact_id: int,
    payload: EpisodeScriptUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.update_creation_artifact(
        session, item, artifact_id, "episode_script", payload.content.model_dump()
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/artifacts/{artifact_id}/publish",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def publish_first_episode(
    session_id: int, artifact_id: int, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    item = await creation_service.get_session(session, session_id, user.id)
    project = await creation_service.publish_first_episode(session, item, artifact_id, user)
    await session.commit()
    return ProjectOut.model_validate(project)


@router.post(
    "/sessions/{session_id}/scene-shot-draft",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_scene_shot_draft(
    session_id: int, session: SessionDep, user: CurrentUser
) -> JobOut:
    item = await creation_service.get_session(session, session_id, user.id)
    job = await creation_service.create_scene_shot_draft_job(session, item)
    await session.commit()
    return JobOut.model_validate(job)


@router.patch(
    "/sessions/{session_id}/artifacts/{artifact_id}/scene-shot-draft",
    response_model=CreationArtifactOut,
)
async def update_scene_shot_draft(
    session_id: int,
    artifact_id: int,
    payload: SceneShotDraftUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> CreationArtifactOut:
    item = await creation_service.get_session(session, session_id, user.id)
    artifact = await creation_service.update_creation_artifact(
        session, item, artifact_id, "scene_shot_draft", payload.content.model_dump()
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)


@router.post(
    "/sessions/{session_id}/artifacts/{artifact_id}/publish-scene-shots",
    response_model=SceneShotPublishOut,
)
async def publish_scene_shot_draft(
    session_id: int, artifact_id: int, session: SessionDep, user: CurrentUser
) -> SceneShotPublishOut:
    item = await creation_service.get_session(session, session_id, user.id)
    result = await creation_service.publish_scene_shot_draft(session, item, artifact_id)
    await session.commit()
    return SceneShotPublishOut.model_validate(result)
