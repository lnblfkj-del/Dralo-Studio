"""Independent edit identity endpoints; no raw document/source-limit writes."""

from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.api.deps import CurrentUser, PageDep, ProjectDep, SessionDep
from app.core.errors import ConflictError
from app.models import EditProject, Job
from app.schemas.edit_project import (
    EditProjectCreate,
    EditProjectOut,
    EditProjectSave,
    EditProjectSummary,
)
from app.schemas.edit_project_asr import EditAsrCreate
from app.schemas.edit_project_export import (
    EditExportCreate,
    EditExportPreflight,
    EditExportPreflightRequest,
)
from app.schemas.job import JobOut
from app.services import edit_project_asr_service
from app.services.edit_project_contract import ProjectEditDocument
from app.services.edit_project_service import (
    create_edit_project,
    get_edit_project,
    list_edit_projects,
    project_output,
    save_edit_project,
)
from app.services.edit_project_source_service import (
    list_project_video_sources,
    verify_project_sources,
)
from app.services.episode_edit_contract import document_fingerprint, validate_document

router = APIRouter(tags=["edit-projects"])


@router.post(
    "/{project_id}/edit-projects/{edit_project_id}/export-preflight",
    response_model=EditExportPreflight,
)
async def export_preflight(
    edit_project_id: int,
    payload: EditExportPreflightRequest,
    project: ProjectDep,
    user: CurrentUser,
    session: SessionDep,
):
    from app.services.edit_project_export_service import preflight

    return await preflight(
        session,
        project_id=project.id,
        edit_project_id=edit_project_id,
        owner_id=user.id,
        payload=payload,
    )


@router.post("/{project_id}/edit-projects/{edit_project_id}/exports", response_model=JobOut)
async def create_export(
    edit_project_id: int,
    payload: EditExportCreate,
    project: ProjectDep,
    user: CurrentUser,
    session: SessionDep,
):
    from app.services.edit_project_export_service import create

    try:
        result = await create(
            session,
            project_id=project.id,
            edit_project_id=edit_project_id,
            owner_id=user.id,
            payload=payload,
        )
        await session.commit()
        return result
    except OperationalError as exc:
        if session.bind.dialect.name != "sqlite" or "locked" not in str(exc.orig).lower():
            raise
        await session.rollback()
        raise ConflictError("导出提交忙, 请重试同一次请求") from exc


@router.get("/{project_id}/edit-projects/{edit_project_id}/exports")
async def export_history(
    edit_project_id: int,
    project: ProjectDep,
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    from app.services.edit_project_export_service import history

    return await history(
        session,
        project_id=project.id,
        edit_project_id=edit_project_id,
        owner_id=user.id,
        offset=offset,
        limit=limit,
    )


@router.get("/{project_id}/edit-fonts", response_model=list[str])
async def edit_fonts(project: ProjectDep, user: CurrentUser):
    from app.services.edit_system_fonts import system_font_families

    return system_font_families()


@router.post("/{project_id}/episodes/{episode_id}/edit-document", response_model=EditProjectOut)
async def open_episode_document(
    episode_id: int, project: ProjectDep, user: CurrentUser, session: SessionDep
):
    from app.services import project_service

    await project_service.get_episode(session, project.id, episode_id)
    # Reopen existing edits; initialization must never refresh an edited timeline.
    existing = await session.scalar(
        select(EditProject)
        .where(
            EditProject.project_id == project.id,
            EditProject.source_episode_id == episode_id,
        )
        .order_by(EditProject.id.asc())
    )
    if existing is not None:
        return await get_edit_project(
            session, project_id=project.id, edit_project_id=existing.id, owner_id=user.id
        )
    # Unlike export, opening the editor must not require every segment to be generated.
    result = await create_edit_project(
        session,
        project_id=project.id,
        owner_id=user.id,
        payload=EditProjectCreate(
            request_id=str(uuid5(NAMESPACE_URL, f"episode-edit:{project.id}:{episode_id}")),
            title="整集剪辑",
            mode="empty",
            frame_rate=24,
        ),
    )
    row = await session.get(EditProject, result.id)
    if row.source_episode_id is None:
        sources = await list_project_video_sources(session, project_id=project.id, owner_id=user.id)
        clips = []
        start = 0
        for source in sources:
            if source["episode_id"] != episode_id:
                continue
            length = round((source["duration"] or 0) * row.frame_rate)
            if length <= 0:
                raise ConflictError("采用视频的时长无效, 请先核对素材")
            clips.append(
                {
                    "clip_id": f"video:{source['video_version_id']}",
                    "track": "video",
                    "lane": 0,
                    "timeline_start_frame": start,
                    "source_in_frame": 0,
                    "source_out_frame": length,
                    "media_file_id": source["media_file_id"],
                    "video_version_id": source["video_version_id"],
                }
            )
            start += length
        document = ProjectEditDocument(
            schema_version=2, frame_rate=row.frame_rate, revision=0, clips=clips
        )
        evidence = {}
        frames = await verify_project_sources(
            session,
            project_id=project.id,
            owner_id=user.id,
            document=document,
            captured_evidence=evidence,
        )
        validate_document(document, frames)
        row.source_episode_id = episode_id
        row.document = document.model_dump(mode="json")
        row.fingerprint = document_fingerprint(document)
        row.source_evidence = evidence
        result = project_output(row)
    await session.commit()
    return result


@router.get("/{project_id}/edit-asr-runtime")
async def asr_runtime(project: ProjectDep):
    return edit_project_asr_service.runtime_status()


@router.post("/{project_id}/edit-projects/{edit_project_id}/asr", response_model=JobOut)
async def create_asr(
    edit_project_id: int,
    payload: EditAsrCreate,
    project: ProjectDep,
    user: CurrentUser,
    session: SessionDep,
):
    try:
        result = await edit_project_asr_service.create(
            session,
            project_id=project.id,
            edit_project_id=edit_project_id,
            owner_id=user.id,
            payload=payload,
        )
    except OperationalError as exc:
        if session.bind.dialect.name != "sqlite" or "locked" not in str(exc.orig).lower():
            raise
        await session.rollback()
        raise ConflictError("Recognition submission is busy; retry the same request") from exc
    await session.commit()
    return result


@router.get("/{project_id}/edit-projects/{edit_project_id}/asr", response_model=list[JobOut])
async def list_asr(
    edit_project_id: int, project: ProjectDep, user: CurrentUser, session: SessionDep
):
    await get_edit_project(
        session, project_id=project.id, edit_project_id=edit_project_id, owner_id=user.id
    )
    return list(
        (
            await session.scalars(
                select(Job)
                .where(
                    Job.project_id == project.id,
                    Job.target_type == edit_project_asr_service.TARGET,
                    Job.target_id == edit_project_id,
                    Job.deleted_at.is_(None),
                )
                .order_by(Job.id.desc())
                .limit(20)
            )
        ).all()
    )


@router.get("/{project_id}/edit-project-sources")
async def sources(project: ProjectDep, user: CurrentUser, session: SessionDep):
    return await list_project_video_sources(session, project_id=project.id, owner_id=user.id)


@router.post(
    "/{project_id}/edit-projects/{edit_project_id}/commands", response_model=EditProjectOut
)
async def save(
    edit_project_id: int,
    payload: EditProjectSave,
    project: ProjectDep,
    user: CurrentUser,
    session: SessionDep,
):
    result = await save_edit_project(
        session,
        project_id=project.id,
        edit_project_id=edit_project_id,
        owner_id=user.id,
        payload=payload,
    )
    await session.commit()
    return result


@router.post("/{project_id}/edit-projects", response_model=EditProjectOut, status_code=201)
async def create(
    payload: EditProjectCreate, project: ProjectDep, user: CurrentUser, session: SessionDep
):
    result = await create_edit_project(
        session, project_id=project.id, owner_id=user.id, payload=payload
    )
    await session.commit()
    return result


@router.get("/{project_id}/edit-projects", response_model=list[EditProjectSummary])
async def list_projects(project: ProjectDep, user: CurrentUser, session: SessionDep, page: PageDep):
    return await list_edit_projects(
        session, project_id=project.id, owner_id=user.id, limit=page.page_size, offset=page.offset
    )


@router.get("/{project_id}/edit-projects/{edit_project_id}", response_model=EditProjectOut)
async def get(edit_project_id: int, project: ProjectDep, user: CurrentUser, session: SessionDep):
    return await get_edit_project(
        session, project_id=project.id, edit_project_id=edit_project_id, owner_id=user.id
    )
