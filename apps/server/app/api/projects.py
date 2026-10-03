"""项目层级路由聚合：Project 根 CRUD 与创建入口，并聚合分集/场景/分镜子路由。

路由只负责参数校验、调用 service 与提交事务，不写查询逻辑。
Project 根之外的路由按资源域拆到 project_*.py，经本 router 统一 include，
保持对外路径与 `/projects` 前缀不变。
"""

from fastapi import APIRouter, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, PageDep, ProjectDep, SessionDep
from app.api.project_creation_routes import router as project_creation_router
from app.api.project_edit_routes import router as project_edit_router
from app.api.project_episode_routes import router as project_episode_router
from app.api.project_scene_shot_routes import router as project_scene_shot_router
from app.core.errors import ProjectDeletionConflictError
from app.services.project_card_service import project_card_summaries
from app.schemas.common import Page
from app.schemas.creation import ProjectFromBrief, ProjectFromIdea
from app.schemas.project import (
    ProjectCreate,
    ProjectCardSummary,
    ProjectFromScript,
    ProjectOut,
    ProjectScriptFinalizeRequest,
    ProjectScriptReadinessOut,
    ProjectUpdate,
)
from app.services import (
    creation_service,
    media_service,
    project_service,
    script_finalization_service,
)

router = APIRouter(prefix="/projects", tags=["projects"])
router.include_router(project_edit_router)


# ---------- Project ----------


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    session: SessionDep,
    user: CurrentUser,
    page: PageDep,
    status_filter: str | None = None,
    keyword: str | None = None,
    include_card_summary: bool = False,
) -> Page[ProjectOut]:
    items, total = await project_service.list_projects(
        session,
        user.id,
        limit=page.page_size,
        offset=page.offset,
        status=status_filter,
        keyword=keyword,
    )
    summaries = await project_card_summaries(session, items) if include_card_summary else {}
    output = [ProjectOut.model_validate(item) for item in items]
    for item in output:
        if item.id in summaries:
            item.card_summary = ProjectCardSummary.model_validate(summaries[item.id])
    return Page[ProjectOut](
        items=output,
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    project = await project_service.create_project(
        session, user, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project: ProjectDep) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.get(
    "/{project_id}/script-readiness", response_model=ProjectScriptReadinessOut
)
async def get_project_script_readiness(
    project: ProjectDep, session: SessionDep
) -> ProjectScriptReadinessOut:
    return ProjectScriptReadinessOut.model_validate(
        await script_finalization_service.get_project_readiness(session, project)
    )


@router.post(
    "/{project_id}/script-finalization", response_model=ProjectScriptReadinessOut
)
async def finalize_project_scripts(
    payload: ProjectScriptFinalizeRequest,
    project: ProjectDep,
    session: SessionDep,
) -> ProjectScriptReadinessOut:
    readiness = await script_finalization_service.finalize_project_scripts(
        session,
        project,
        [item.model_dump() for item in payload.episodes],
    )
    await session.commit()
    return ProjectScriptReadinessOut.model_validate(readiness)


@router.post("/from-script", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project_from_script(
    payload: ProjectFromScript, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    settings = payload.creation_settings.model_dump()
    settings.update({"source_type": "upload", "reference_text": payload.script})
    project = await project_service.create_project_from_script(
        session,
        user,
        payload.name,
        payload.script,
        settings,
        [marker.model_dump() for marker in payload.episode_markers],
    )
    settings = dict(project.creation_settings or settings)
    await creation_service.create_session(
        session,
        user.id,
        title=payload.name,
        brief=payload.script,
        settings=settings,
        project_id=project.id,
    )
    # Local episode detection and AI study are separate stages. The legacy
    # auto_optimize field remains accepted, but no model job starts implicitly.
    await session.commit()
    return ProjectOut.model_validate(project)


@router.post("/from-brief", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project_from_brief(
    payload: ProjectFromBrief, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    project = await project_service.create_project_from_brief(
        session, user, payload.name, payload.creation_settings.model_dump()
    )
    await creation_service.create_session(
        session,
        user.id,
        title=payload.name,
        brief=payload.creation_settings.brief or payload.creation_settings.reference_text,
        settings=dict(project.creation_settings or {}),
        project_id=project.id,
    )
    await session.commit()
    return ProjectOut.model_validate(project)


@router.post("/from-idea", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project_from_idea(
    payload: ProjectFromIdea, session: SessionDep, user: CurrentUser
) -> ProjectOut:
    settings = {
        "source_type": "idea",
        "brief": payload.brief.strip(),
        "episode_count": 10,
    }
    project = await project_service.create_project_from_brief(
        session, user, payload.name, settings
    )
    await creation_service.create_session(
        session,
        user.id,
        title=payload.name,
        brief=payload.brief,
        settings=dict(project.creation_settings or settings),
        project_id=project.id,
    )
    await session.commit()
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    payload: ProjectUpdate, project: ProjectDep, session: SessionDep
) -> ProjectOut:
    updated = await project_service.update_project(
        session, project, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ProjectOut.model_validate(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project: ProjectDep, session: SessionDep) -> None:
    try:
        media_paths = await project_service.delete_project(session, project)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProjectDeletionConflictError(
            "项目仍有未清理的媒体或业务关联，删除未完成；请刷新后重试"
        ) from exc
    media_service.delete_files(media_paths)


# ---------- 子路由聚合 ----------

router.include_router(project_creation_router)
router.include_router(project_episode_router)
router.include_router(project_scene_shot_router)
