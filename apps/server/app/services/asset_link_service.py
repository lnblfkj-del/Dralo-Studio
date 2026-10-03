"""Global asset links and shot usage relationships."""

import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, AssetUsage, AssetVersion, Episode, Project, ProjectAssetLink, Scene, Shot
from app.services.asset_catalog_service import FRAME_VIEW_TYPES, _asset_outs_batch, ensure_slug_available, get_project_link
from app.services.team_access import owner_scope, same_team


async def list_global_assets(session: AsyncSession, owner_id: int, asset_type: str | None = None) -> list[dict[str, Any]]:
    statement = select(Asset).where(owner_scope(Asset.owner_id, owner_id), Asset.project_id.is_(None))
    if asset_type is not None:
        statement = statement.where(Asset.asset_type == asset_type)
    assets = list((await session.execute(statement.order_by(Asset.id.desc()))).scalars())
    return await _asset_outs_batch(session, [(asset, None) for asset in assets])


async def get_owned_global_asset(session: AsyncSession, owner_id: int, asset_id: int) -> Asset:
    asset = await session.scalar(select(Asset).where(Asset.id == asset_id, owner_scope(Asset.owner_id, owner_id), Asset.project_id.is_(None)))
    if asset is None:
        raise NotFoundError("全局资产不存在")
    return asset


async def create_global_asset(session: AsyncSession, owner_id: int, data: dict[str, Any]) -> Asset:
    slug = data["slug"]
    if not slug or not re.fullmatch(r"[\w\u4e00-\u9fff-]+", slug):
        raise ConflictError("引用名称只能包含文字、数字、下划线或连字符")
    exists = await session.scalar(select(Asset.id).where(owner_scope(Asset.owner_id, owner_id), Asset.project_id.is_(None), Asset.slug == slug))
    if exists is not None:
        raise ConflictError(f"全局引用名称 @{slug} 已存在")
    asset = Asset(project_id=None, owner_id=owner_id, **data)
    session.add(asset)
    await session.flush()
    return asset


async def link_global_asset(session: AsyncSession, project: Project, asset_id: int, local_slug: str | None) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None or not await same_team(session, asset.owner_id, project.owner_id) or asset.project_id is not None:
        raise NotFoundError("全局资产不存在")
    exists = await session.scalar(select(ProjectAssetLink.id).where(ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == asset.id))
    if exists is not None:
        raise ConflictError("资产已加入当前项目")
    alias = (local_slug or asset.slug).strip().removeprefix("@")
    await ensure_slug_available(session, project.id, alias)
    session.add(ProjectAssetLink(project_id=project.id, asset_id=asset.id, local_slug=alias))
    await session.flush()
    return asset


async def unlink_global_asset(session: AsyncSession, project_id: int, asset: Asset) -> None:
    if asset.project_id == project_id:
        raise ConflictError("项目自有资产请使用删除操作")
    in_use = await session.scalar(select(AssetUsage.id).where(AssetUsage.project_id == project_id, AssetUsage.asset_id == asset.id).limit(1))
    if in_use is not None:
        raise ConflictError("资产仍被项目分镜引用，请先解除使用关系")
    link = await get_project_link(session, project_id, asset.id)
    await session.delete(link)
    await session.flush()


async def list_asset_usages(session: AsyncSession, project_id: int, asset_id: int) -> list[dict[str, Any]]:
    rows = (await session.execute(select(AssetUsage, Episode.number, Scene.name, Shot.order).join(Episode, Episode.id == AssetUsage.episode_id).join(Scene, Scene.id == AssetUsage.scene_id).join(Shot, Shot.id == AssetUsage.shot_id).where(AssetUsage.project_id == project_id, AssetUsage.asset_id == asset_id).order_by(Episode.number, Scene.order, Shot.order))).all()
    return [{"id": usage.id, "project_id": usage.project_id, "asset_id": usage.asset_id, "episode_id": usage.episode_id, "episode_number": episode_number, "scene_id": usage.scene_id, "scene_name": scene_name, "shot_id": usage.shot_id, "shot_order": shot_order, "asset_version_id": usage.asset_version_id, "usage_type": usage.usage_type, "created_at": usage.created_at} for usage, episode_number, scene_name, shot_order in rows]


async def replace_asset_usages(session: AsyncSession, project: Project, asset: Asset, usages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [(item["shot_id"], item["usage_type"]) for item in usages]
    if len(keys) != len(set(keys)):
        raise ConflictError("同一分镜的资产用途不能重复")
    frame_usages = [item for item in usages if item["usage_type"] in FRAME_VIEW_TYPES]
    if frame_usages and asset.asset_type != "reference":
        raise ConflictError("首帧、尾帧和关键帧只能绑定镜头帧资产")
    if any(not item.get("asset_version_id") for item in frame_usages):
        raise ConflictError("镜头帧关系必须固定到具体素材版本")
    shot_ids = {item["shot_id"] for item in usages}
    rows = (await session.execute(select(Shot, Scene, Episode).join(Scene, Scene.id == Shot.scene_id).join(Episode, Episode.id == Scene.episode_id).where(Shot.id.in_(shot_ids), Episode.project_id == project.id))).all() if shot_ids else []
    by_shot = {shot.id: (shot, scene, episode) for shot, scene, episode in rows}
    if set(by_shot) != shot_ids:
        raise NotFoundError("引用分镜不存在")
    version_ids = {item["asset_version_id"] for item in usages if item.get("asset_version_id")}
    versions_by_id: dict[int, AssetVersion] = {}
    if version_ids:
        found = list((await session.execute(select(AssetVersion).where(AssetVersion.id.in_(version_ids), AssetVersion.asset_id == asset.id))).scalars())
        versions_by_id = {version.id: version for version in found}
        if set(versions_by_id) != version_ids:
            raise NotFoundError("资产版本不存在")
    if any(versions_by_id[item["asset_version_id"]].view_type != item["usage_type"] for item in frame_usages):
        raise ConflictError("镜头帧用途必须与所选素材版本分类一致")
    previous = list((await session.execute(select(AssetUsage).where(AssetUsage.project_id == project.id, AssetUsage.asset_id == asset.id))).scalars())
    affected_ids = {usage.shot_id for usage in previous if usage.shot_id is not None} | shot_ids
    affected_shots = list((await session.execute(select(Shot).where(Shot.id.in_(affected_ids)))).scalars()) if affected_ids else []
    await session.execute(delete(AssetUsage).where(AssetUsage.project_id == project.id, AssetUsage.asset_id == asset.id))
    for item in usages:
        _shot, scene, episode = by_shot[item["shot_id"]]
        session.add(AssetUsage(project_id=project.id, asset_id=asset.id, episode_id=episode.id, scene_id=scene.id, shot_id=item["shot_id"], asset_version_id=item.get("asset_version_id"), usage_type=item["usage_type"]))
    for shot in affected_shots:
        refs = dict(shot.refs or {})
        asset_ids = set(refs.get("asset_ids", []))
        if shot.id in shot_ids:
            asset_ids.add(asset.id)
        else:
            asset_ids.discard(asset.id)
        refs["asset_ids"] = sorted(asset_ids)
        shot.refs = refs
    await session.flush()
    return await list_asset_usages(session, project.id, asset.id)
