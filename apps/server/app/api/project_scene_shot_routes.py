"""项目场景与分镜路由：Scene / Shot 的层级 CRUD 与视频版本选择。

路由只负责参数校验、调用 service 与提交事务，不写查询逻辑。
路径相对 `/projects` 前缀，由 projects.router 统一 include。
"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.api.deps import EpisodeDep, SceneDep, SessionDep, ShotDep
from app.schemas.project import (
    SceneCreate,
    SceneOut,
    SceneUpdate,
    ShotCreate,
    ShotOut,
    ShotUpdate,
    ShotVideoVersionOut,
)
from app.services import project_service

router = APIRouter(tags=["projects"])

_SCENE_BASE = "/{project_id}/episodes/{episode_id}/scenes"
_SHOT_BASE = _SCENE_BASE + "/{scene_id}/shots"


class ReorderScenesRequest(BaseModel):
    scene_ids: list[int] = Field(min_length=1)


class ReorderShotsRequest(BaseModel):
    """按给定顺序重排镜头，需提供该场景全部镜头 ID。"""

    shot_ids: list[int] = Field(min_length=1)


@router.post(_SCENE_BASE + "/reorder", response_model=list[SceneOut])
async def reorder_scenes(
    payload: ReorderScenesRequest, episode: EpisodeDep, session: SessionDep
) -> list[SceneOut]:
    scenes = await project_service.reorder_scenes(session, episode.id, payload.scene_ids)
    await session.commit()
    return [SceneOut.model_validate(item) for item in scenes]


@router.get(_SCENE_BASE, response_model=list[SceneOut])
async def list_scenes(episode: EpisodeDep, session: SessionDep) -> list[SceneOut]:
    scenes = await project_service.list_scenes(session, episode.id)
    return [SceneOut.model_validate(item) for item in scenes]


@router.post(_SCENE_BASE, response_model=SceneOut, status_code=status.HTTP_201_CREATED)
async def create_scene(
    payload: SceneCreate, episode: EpisodeDep, session: SessionDep
) -> SceneOut:
    scene = await project_service.create_scene(
        session, episode, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return SceneOut.model_validate(scene)


@router.get(_SCENE_BASE + "/{scene_id}", response_model=SceneOut)
async def get_scene(scene: SceneDep) -> SceneOut:
    return SceneOut.model_validate(scene)


@router.patch(_SCENE_BASE + "/{scene_id}", response_model=SceneOut)
async def update_scene(
    payload: SceneUpdate, scene: SceneDep, session: SessionDep
) -> SceneOut:
    updated = await project_service.update_scene(
        session, scene, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return SceneOut.model_validate(updated)


@router.delete(_SCENE_BASE + "/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(scene: SceneDep, session: SessionDep) -> None:
    await project_service.delete_scene(session, scene)
    await session.commit()


@router.get(_SHOT_BASE, response_model=list[ShotOut])
async def list_shots(scene: SceneDep, session: SessionDep) -> list[ShotOut]:
    shots = await project_service.list_shots(session, scene.id)
    return [ShotOut.model_validate(item) for item in shots]


@router.post(_SHOT_BASE, response_model=ShotOut, status_code=status.HTTP_201_CREATED)
async def create_shot(
    payload: ShotCreate, scene: SceneDep, session: SessionDep
) -> ShotOut:
    shot = await project_service.create_shot(
        session, scene, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ShotOut.model_validate(shot)


@router.post(_SHOT_BASE + "/reorder", response_model=list[ShotOut])
async def reorder_shots(
    payload: ReorderShotsRequest, scene: SceneDep, session: SessionDep
) -> list[ShotOut]:
    shots = await project_service.reorder_shots(session, scene.id, payload.shot_ids)
    await session.commit()
    return [ShotOut.model_validate(item) for item in shots]


@router.get(_SHOT_BASE + "/{shot_id}", response_model=ShotOut)
async def get_shot(shot: ShotDep) -> ShotOut:
    return ShotOut.model_validate(shot)


@router.get(_SHOT_BASE + "/{shot_id}/video-versions", response_model=list[ShotVideoVersionOut])
async def list_shot_video_versions(shot: ShotDep, session: SessionDep) -> list[ShotVideoVersionOut]:
    versions = await project_service.list_shot_video_versions(session, shot)
    return [ShotVideoVersionOut.model_validate(item) for item in versions]


@router.post(_SHOT_BASE + "/{shot_id}/video-versions/{version_id}/select", response_model=ShotVideoVersionOut)
async def select_shot_video_version(
    version_id: int, shot: ShotDep, session: SessionDep
) -> ShotVideoVersionOut:
    version = await project_service.select_shot_video_version(session, shot, version_id)
    await session.commit()
    return ShotVideoVersionOut.model_validate(version)


@router.patch(_SHOT_BASE + "/{shot_id}", response_model=ShotOut)
async def update_shot(
    payload: ShotUpdate, shot: ShotDep, session: SessionDep
) -> ShotOut:
    updated = await project_service.update_shot(
        session, shot, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ShotOut.model_validate(updated)


@router.delete(_SHOT_BASE + "/{shot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shot(shot: ShotDep, session: SessionDep) -> None:
    await project_service.delete_shot(session, shot)
    await session.commit()
