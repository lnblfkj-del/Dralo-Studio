"""Asset version upload, review, and final-version selection."""

from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, AssetVersion, MediaFile
from app.services.asset_catalog_service import (
    LAYOUT_SHEET_VIEW_TYPE,
    normalize_asset_view,
)


async def add_uploaded_asset_version(
    session: AsyncSession,
    asset: Asset,
    media: MediaFile,
    *,
    view_type: str | None = None,
    view_label: str | None = None,
) -> AssetVersion:
    next_version = int(await session.scalar(select(func.coalesce(func.max(AssetVersion.version), 0)).where(AssetVersion.asset_id == asset.id))) + 1
    resolved_type, resolved_label = normalize_asset_view(asset.asset_type, view_type, view_label)
    version = AssetVersion(
        asset_id=asset.id,
        media_file_id=media.id,
        version=next_version,
        prompt=asset.prompt_anchor or asset.description or asset.name,
        parameters={"source": "upload"},
        view_label=resolved_label,
        view_type=resolved_type,
        review_status="approved" if next_version == 1 and resolved_type != LAYOUT_SHEET_VIEW_TYPE else "candidate",
        tags=[],
        is_final=next_version == 1 and resolved_type != LAYOUT_SHEET_VIEW_TYPE,
    )
    session.add(version)
    await session.flush()
    return version


async def delete_asset_version(session: AsyncSession, asset: Asset, version_id: int) -> Path | None:
    from app.services.asset_production_service import guard_delete
    await guard_delete(session, asset.id, version_id)
    version = await session.scalar(select(AssetVersion).where(AssetVersion.id == version_id, AssetVersion.asset_id == asset.id))
    if version is None:
        raise NotFoundError("资产作品版本不存在")
    media = await session.get(MediaFile, version.media_file_id)
    was_final = version.is_final
    await session.delete(version)
    await session.flush()
    if was_final:
        replacement = await session.scalar(select(AssetVersion).where(AssetVersion.asset_id == asset.id, AssetVersion.view_type != LAYOUT_SHEET_VIEW_TYPE, AssetVersion.review_status != "archived").order_by(AssetVersion.version.desc()).limit(1))
        if replacement is not None:
            replacement.is_final = True
    from app.core.config import settings
    if media is None or settings.runtime_execution_location == "cloud":
        await session.flush()
        return None
    still_referenced = await session.scalar(select(AssetVersion.id).where(AssetVersion.media_file_id == media.id).limit(1))
    if still_referenced is not None:
        await session.flush()
        return None
    path = Path(media.file_path)
    await session.delete(media)
    await session.flush()
    return path


async def set_final_version(session: AsyncSession, asset: Asset, version_id: int) -> AssetVersion:
    version = await session.get(AssetVersion, version_id)
    if version is None or version.asset_id != asset.id:
        raise NotFoundError("资产版本不存在")
    if version.review_status == "archived":
        raise ConflictError("已归档的资产视图不能设为制作版本")
    if version.view_type == LAYOUT_SHEET_VIEW_TYPE:
        raise ConflictError("排版预览需先拆分为独立素材，不能设为最终制作版本")
    await session.execute(update(AssetVersion).where(AssetVersion.asset_id == asset.id).values(is_final=False))
    version.is_final = True
    version.review_status = "approved"
    await session.flush()
    return version


async def update_asset_version(session: AsyncSession, asset: Asset, version_id: int, data: dict[str, object]) -> AssetVersion:
    version = await session.scalar(select(AssetVersion).where(AssetVersion.id == version_id, AssetVersion.asset_id == asset.id))
    if version is None:
        raise NotFoundError("资产版本不存在")
    if data.get("review_status") == "archived" and version.is_final:
        raise ConflictError("当前制作版本不能直接归档，请先选择其他版本")
    if "view_type" in data or "view_label" in data:
        resolved_type, resolved_label = normalize_asset_view(asset.asset_type, data.get("view_type", version.view_type), data.get("view_label", version.view_label))
        if resolved_type == LAYOUT_SHEET_VIEW_TYPE:
            if version.is_final:
                raise ConflictError("当前最终制作版本不能改为排版预览，请先选择其他最终版本")
            from app.services.asset_production_service import version_is_adopted
            if await version_is_adopted(session, asset.id, version.id):
                raise ConflictError("已采用的素材版本不能改为排版预览，请先解除项目采用")
        data = {**data, "view_type": resolved_type, "view_label": resolved_label}
    for key, value in data.items():
        if value is not None:
            setattr(version, key, value)
    await session.flush()
    return version

