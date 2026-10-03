"""Resolve project-local asset adoptions into immutable generation bindings."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.models import AssetVersion, MediaFile, Project
from app.services import asset_service
from app.services.asset_production_service import link_for
from app.services.team_access import same_team


def binding_role(binding: dict[str, Any]) -> str:
    role = binding.get("role") or binding.get("usage_type") or "reference_image"
    if role == "voice":
        return "voice_reference"
    if role in {"audio", "audio_track"}:
        return "audio_reference"
    if role in {"first_frame", "last_frame", "audio_reference", "voice_reference"}:
        return role
    return "reference_image"


def _expected_kind(asset_type: str, role: str | None) -> str:
    if role in {"audio_reference", "voice_reference"} or asset_type == "voice":
        return "audio"
    if asset_type == "video":
        return "video"
    return "image"


def _preferred_keys(asset_type: str, role: str | None) -> tuple[str, ...]:
    if role in {"first_frame", "last_frame"}:
        return (role, f"frame:{role}", "default")
    if asset_type == "voice":
        return ("voice:default", "default")
    if asset_type in {"character", "costume"}:
        return ("appearance:default", "appearance:base", "default")
    return ("default",)


async def resolve_asset_binding(
    session: AsyncSession,
    project: Project,
    *,
    asset_id: int,
    adoption_key: str | None = None,
    asset_version_id: int | None = None,
    media_file_id: int | None = None,
    role: str | None = None,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    """Resolve one exact project adoption and reject stale caller identities."""
    asset = await asset_service.get_asset(session, project.id, asset_id)
    link = await link_for(session, project.id, asset.id)
    if link.production_archived:
        raise ConflictError(f"资产 {asset.name} 已归档，请先恢复")

    data = link.production_data or {}
    adoption_map_exists = "adoptions" in data
    adoptions = data.get("adoptions", [])
    if not isinstance(adoptions, list):
        raise ConflictError(f"资产 {asset.name} 的项目采用记录已损坏")

    choice: dict[str, Any] | None = None
    if adoption_map_exists:
        if adoption_key:
            choice = next((item for item in adoptions if item.get("key") == adoption_key), None)
            if choice is None:
                raise ConflictError(f"资产 {asset.name} 尚未采用用途 {adoption_key}")
        elif asset_version_id is not None:
            matches = [item for item in adoptions if item.get("version_id") == asset_version_id]
            if len(matches) != 1:
                raise ConflictError(f"资产 {asset.name} 的版本未被当前项目唯一采用")
            choice = matches[0]
        else:
            for key in _preferred_keys(asset.asset_type, role):
                choice = next((item for item in adoptions if item.get("key") == key), None)
                if choice is not None:
                    break
            if choice is None and len(adoptions) == 1:
                choice = adoptions[0]
            if choice is None:
                raise ConflictError(f"资产 {asset.name} 有多个采用用途，请明确选择")

        try:
            chosen_version_id = int(choice["version_id"])
            chosen_media_id = int(choice["media_file_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError(f"资产 {asset.name} 的项目采用记录已损坏") from exc
        if asset_version_id is not None and asset_version_id != chosen_version_id:
            raise ValidationError(f"资产 {asset.name} 的版本与采用用途不匹配")
        if media_file_id is not None and media_file_id != chosen_media_id:
            raise ValidationError(f"资产 {asset.name} 的媒体版本已变化，请刷新")
        resolved_key = str(choice.get("key") or adoption_key or "default")
        origin = str(choice.get("origin") or "explicit")
    else:
        version = await session.get(AssetVersion, asset_version_id) if asset_version_id else None
        if version is None and asset_version_id is None:
            version = await session.scalar(
                select(AssetVersion)
                .where(
                    AssetVersion.asset_id == asset.id,
                    AssetVersion.is_final.is_(True),
                    AssetVersion.review_status != "archived",
                    AssetVersion.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE,
                )
                .order_by(AssetVersion.version.desc())
                .limit(1)
            )
        if version is None:
            raise ConflictError(f"资产 {asset.name} 尚未选择可用版本")
        chosen_version_id = version.id
        chosen_media_id = version.media_file_id
        resolved_key = adoption_key or (
            role if role in {"first_frame", "last_frame"} else "default"
        )
        origin = "legacy_final"

    version = await session.get(AssetVersion, chosen_version_id)
    if (
        version is None
        or version.asset_id != asset.id
        or version.review_status == "archived"
        or version.view_type == asset_service.LAYOUT_SHEET_VIEW_TYPE
    ):
        raise ConflictError(f"资产 {asset.name} 的采用版本已失效或已归档")
    if not adoption_map_exists and not (
        version.is_final or version.review_status == "approved"
    ):
        raise ConflictError(f"资产 {asset.name} 的旧版素材尚未审核通过")
    if version.media_file_id != chosen_media_id:
        raise ConflictError(f"资产 {asset.name} 的采用媒体与版本记录不一致")
    if role in {"first_frame", "last_frame"} and version.view_type != role:
        raise ValidationError(f"资产 {asset.name} 的版本分类与 {role} 用途不一致")
    if role in {"audio_reference", "voice_reference"} and asset.asset_type != "voice":
        raise ValidationError(f"资产 {asset.name} 不是声音资产")

    media = await session.get(MediaFile, version.media_file_id)
    kind = expected_kind or _expected_kind(asset.asset_type, role)
    if media is None or not await same_team(session, project.owner_id, media.owner_id):
        raise ConflictError(f"资产 {asset.name} 的采用媒体不存在或无权访问")
    if media.kind != kind:
        raise ValidationError(f"资产 {asset.name} 的用途需要 {kind} 素材")
    if media_file_id is not None and media.id != media_file_id:
        raise ValidationError(f"资产 {asset.name} 的媒体版本已变化，请刷新")

    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "asset_type": asset.asset_type,
        "adoption_key": resolved_key,
        "asset_version_id": version.id,
        "media_file_id": media.id,
        "media_kind": media.kind,
        "view_type": version.view_type,
        "view_label": version.view_label,
        "adoption_origin": origin,
        "resolved_revision": link.production_revision,
        "role": role or "reference_image",
        "resolved": True,
    }
