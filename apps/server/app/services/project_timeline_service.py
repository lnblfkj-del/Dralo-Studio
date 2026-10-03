"""Episode mutation and scene/shot timeline operations."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Episode, Scene, Shot, ShotVideoVersion


async def update_episode(
    session: AsyncSession, episode: Episode, data: dict[str, Any], actor_id: int | None = None
) -> Episode:
    from app.services.script_version_service import save_script

    data = dict(data)
    if episode.status == "archived" or data.get("status") == "archived":
        raise ConflictError("分集归档与恢复请通过大纲确认流程操作")
    expected = data.pop("expected_script_revision", episode.script_revision)
    note = data.pop("version_note", None)
    previous_duration = episode.duration_estimate
    script_changed = "script" in data and (data.get("script") or "") != (episode.script or "")
    if "script" in data:
        await save_script(session, episode, data.pop("script"), expected=expected, actor_id=actor_id, note=note)
    new_number = data.get("number")
    if new_number is not None and new_number != episode.number:
        exists = await session.execute(
            select(Episode.id).where(
                Episode.project_id == episode.project_id,
                Episode.number == new_number,
                Episode.id != episode.id,
            )
        )
        if exists.scalar_one_or_none() is not None:
            raise ConflictError(f"第 {new_number} 集已存在")

    number_changed = new_number is not None and new_number != episode.number
    for key, value in data.items():
        setattr(episode, key, value)
    await session.flush()
    if script_changed:
        from app.services.story_continuity_service import sync_episode_continuity_records

        await sync_episode_continuity_records(
            session, episode, confirmation_status="draft"
        )
    if number_changed:
        from app.services.script_finalization_service import invalidate_project_structure

        await invalidate_project_structure(
            session, episode.project_id, f"分集编号调整为第 {episode.number} 集，全集结构已变化"
        )
    if (
        not script_changed
        and "duration_estimate" in data
        and data["duration_estimate"] != previous_duration
    ):
        from app.services.script_finalization_service import invalidate_episode_duration_dependency

        await invalidate_episode_duration_dependency(session, episode, previous_duration)
    return episode


async def delete_episode(session: AsyncSession, episode: Episode) -> None:
    await reject_locked_descendants(session, episode_id=episode.id)
    from app.services.script_finalization_service import invalidate_project_structure

    await invalidate_project_structure(
        session, episode.project_id, f"删除第 {episode.number} 集，全集结构已变化"
    )
    await session.delete(episode)
    await session.flush()


# ---------- Scene ----------


async def list_scenes(session: AsyncSession, episode_id: int) -> list[Scene]:
    stmt = (
        select(Scene)
        .where(Scene.episode_id == episode_id)
        .order_by(Scene.order.asc(), Scene.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_scene(session: AsyncSession, episode_id: int, scene_id: int) -> Scene:
    scene = await session.get(Scene, scene_id)
    if scene is None or scene.episode_id != episode_id:
        raise NotFoundError("场景不存在")
    return scene


async def create_scene(
    session: AsyncSession, episode: Episode, data: dict[str, Any]
) -> Scene:
    if "order" not in data:
        last = await session.scalar(select(func.max(Scene.order)).where(Scene.episode_id == episode.id))
        data = {**data, "order": 0 if last is None else last + 1}
    scene = Scene(episode_id=episode.id, owner_id=episode.owner_id, **data)
    session.add(scene)
    await session.flush()
    return scene


async def update_scene(
    session: AsyncSession, scene: Scene, data: dict[str, Any]
) -> Scene:
    for key, value in data.items():
        setattr(scene, key, value)
    await session.flush()
    return scene


async def delete_scene(session: AsyncSession, scene: Scene) -> None:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    historical = await session.scalar(select(Shot.id).where(
        Shot.scene_id == scene.id, Shot.status == SHOT_STATUS_SUPERSEDED,
    ).limit(1))
    if historical is not None:
        raise ConflictError("场景包含历史镜头或视频，不能直接删除")
    await reject_locked_descendants(session, scene_id=scene.id)
    await session.delete(scene)
    await session.flush()


async def reorder_scenes(
    session: AsyncSession, episode_id: int, scene_ids: list[int]
) -> list[Scene]:
    scenes = await list_scenes(session, episode_id)
    if len(scene_ids) != len(scenes) or set(scene_ids) != {scene.id for scene in scenes}:
        raise ConflictError("分场列表已变化或包含重复项，请刷新后重试")
    by_id = {scene.id: scene for scene in scenes}
    for index, scene_id in enumerate(scene_ids):
        by_id[scene_id].order = index
    await session.flush()
    return await list_scenes(session, episode_id)


async def reject_locked_descendants(
    session: AsyncSession,
    *,
    project_id: int | None = None,
    episode_id: int | None = None,
    scene_id: int | None = None,
) -> None:
    """父级级联删除也必须遵守镜头锁，检查通过前不修改任何记录。"""
    stmt = select(Shot.id).join(Scene).join(Episode).where(Shot.is_locked.is_(True))
    if project_id is not None:
        stmt = stmt.where(Episode.project_id == project_id)
    if episode_id is not None:
        stmt = stmt.where(Scene.episode_id == episode_id)
    if scene_id is not None:
        stmt = stmt.where(Shot.scene_id == scene_id)
    if await session.scalar(stmt.limit(1)) is not None:
        raise ConflictError("包含已锁定分镜，请先逐一解锁后再删除")


# ---------- Shot ----------


async def list_shots(session: AsyncSession, scene_id: int) -> list[Shot]:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    stmt = (
        select(Shot)
        .where(Shot.scene_id == scene_id, Shot.status != SHOT_STATUS_SUPERSEDED)
        .order_by(Shot.order.asc(), Shot.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_shot(session: AsyncSession, scene_id: int, shot_id: int) -> Shot:
    shot = await session.get(Shot, shot_id)
    if shot is None or shot.scene_id != scene_id:
        raise NotFoundError("分镜不存在")
    return shot


async def create_shot(
    session: AsyncSession, scene: Scene, data: dict[str, Any]
) -> Shot:
    if "order" not in data:
        last = await session.scalar(select(func.max(Shot.order)).where(Shot.scene_id == scene.id))
        data = {**data, "order": 0 if last is None else last + 1}
    shot = Shot(scene_id=scene.id, owner_id=scene.owner_id, **data)
    session.add(shot)
    await session.flush()
    return shot


async def update_shot(
    session: AsyncSession, shot: Shot, data: dict[str, Any]
) -> Shot:
    """更新镜头。

    锁定的镜头只允许改 is_locked 本身（用于解锁），其余字段一律拒绝。
    见 PROJECT_SPEC.md 29。
    """
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    if shot.status == SHOT_STATUS_SUPERSEDED:
        raise ConflictError("历史镜头不能修改，请在当前计划中编辑")
    if shot.is_locked and set(data.keys()) - {"is_locked"}:
        raise ConflictError("分镜已锁定，请先解锁后再修改")

    for key, value in data.items():
        setattr(shot, key, value)
    await session.flush()
    return shot


async def delete_shot(session: AsyncSession, shot: Shot) -> None:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    if shot.status == SHOT_STATUS_SUPERSEDED:
        raise ConflictError("历史镜头不能删除")
    if shot.is_locked:
        raise ConflictError("分镜已锁定，请先解锁后再删除")
    await session.delete(shot)
    await session.flush()


async def list_shot_video_versions(session: AsyncSession, shot: Shot) -> list[ShotVideoVersion]:
    return list((await session.execute(
        select(ShotVideoVersion).where(ShotVideoVersion.shot_id == shot.id)
        .order_by(ShotVideoVersion.version.desc())
    )).scalars())


async def select_shot_video_version(
    session: AsyncSession, shot: Shot, version_id: int
) -> ShotVideoVersion:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    if shot.status == SHOT_STATUS_SUPERSEDED:
        raise ConflictError("历史镜头视频不可在当前计划中重新采用")
    version = await session.scalar(select(ShotVideoVersion).where(
        ShotVideoVersion.id == version_id, ShotVideoVersion.shot_id == shot.id
    ))
    if version is None:
        raise NotFoundError("视频版本不存在")
    await session.execute(update(ShotVideoVersion).where(
        ShotVideoVersion.shot_id == shot.id
    ).values(is_final=False))
    version.is_final = True
    shot.status = "ready"
    await session.flush()
    from app.services.segment_plan_service import mirror_legacy_shot_version

    await mirror_legacy_shot_version(session, shot, version)
    return version


async def reorder_shots(
    session: AsyncSession, scene_id: int, shot_ids: list[int]
) -> list[Shot]:
    """按给定顺序重排镜头。

    只接受与该场景现有镜头完全一致的 ID 集合，避免漏排或串场景。
    """
    shots = await list_shots(session, scene_id)
    existing = {shot.id for shot in shots}
    if len(shot_ids) != len(shots) or existing != set(shot_ids):
        raise ConflictError("分镜列表已变化，请刷新后重试")

    if any(shot.is_locked for shot in shots):
        raise ConflictError("本场包含已锁定分镜，请全部解锁后再排序")

    by_id = {shot.id: shot for shot in shots}
    for index, shot_id in enumerate(shot_ids):
        by_id[shot_id].order = index
    await session.flush()
    return await list_shots(session, scene_id)
