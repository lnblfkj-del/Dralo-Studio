"""Project-adopted audio, never a guessed final version or voice-design preview."""

# ruff: noqa: RUF001
import math

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import Asset, AssetVersion, MediaFile, ProjectAssetLink, ProjectMediaLink
from app.services.asset_audio_profile import effective_profile
from app.services.audio_purpose import is_reference
from app.services.team_access import owner_scope

CATEGORIES = {"voice":"配音", "music":"BGM", "ambience":"环境音", "sfx":"音效",
    "bgm":"BGM", "character_voice":"配音", "sound_effect":"音效"}
PRODUCTION_CATEGORIES = {"dialogue_audio":"配音", "music":"BGM", "ambience":"环境音", "sfx":"音效"}
LIMIT = 5000


async def is_adopted(session, project_id, media_id):
    rows = (await session.execute(select(AssetVersion, ProjectAssetLink, Asset.project_id)
        .join(Asset, Asset.id == AssetVersion.asset_id).outerjoin(ProjectAssetLink,
            (ProjectAssetLink.asset_id == AssetVersion.asset_id) & (ProjectAssetLink.project_id == project_id))
        .where(AssetVersion.media_file_id == media_id, Asset.asset_type == "voice",
        AssetVersion.review_status != "archived"))).all()
    for version, link, origin_project_id in rows:
        if link is None:
            if origin_project_id == project_id and version.is_final:
                return True
            continue
        if link.production_archived:
            continue
        data = link.production_data or {}
        if "adoptions" in data:
            if any(item.get("version_id") == version.id for item in data.get("adoptions", [])):
                return True
        elif version.is_final:
            return True
    return False


async def pin_verified_audio_sources(session, project_id, document):
    """Call only after source verification, inside the editor's write transaction."""
    from app.models import Project
    from app.services.media_core_service import link_media_to_project

    ids = {clip.media_file_id for clip in document.clips
        if clip.track in {"bgm", "dialogue", "ambience", "sfx"} and clip.media_file_id is not None}
    if not ids:
        return
    rows = (await session.scalars(select(MediaFile).where(MediaFile.id.in_(ids), MediaFile.kind == "audio"))).all()
    project = await session.get(Project, project_id)
    for media in rows:
        if media.project_id != project_id:
            await link_media_to_project(session, media, project)


async def has_audio_asset_reference(session, project_id, media_id):
    return await session.scalar(select(ProjectAssetLink.id).join(AssetVersion,
        AssetVersion.asset_id == ProjectAssetLink.asset_id).join(Asset, Asset.id == AssetVersion.asset_id)
        .where(ProjectAssetLink.project_id == project_id, AssetVersion.media_file_id == media_id,
            Asset.asset_type == "voice").limit(1)) is not None


def available(media):
    if media is None or media.kind != "audio" or not isinstance(media.duration, (int, float)) or not math.isfinite(media.duration) or media.duration <= 0:
        return False
    try:
        root = settings.storage_path.resolve()
        path = (root / media.file_path).resolve()
        return path.is_relative_to(root) and path.is_file()
    except (OSError, RuntimeError):
        return False


async def library(session, project):
    rows = (await session.execute(select(Asset, ProjectAssetLink).outerjoin(ProjectAssetLink,
        (ProjectAssetLink.asset_id == Asset.id) & (ProjectAssetLink.project_id == project.id))
        .where(or_(Asset.project_id == project.id, ProjectAssetLink.id.is_not(None)),
        Asset.asset_type == "voice", owner_scope(Asset.owner_id, project.owner_id)).limit(LIMIT+1))).all()
    if len(rows) > LIMIT:
        raise ConflictError("声音资产目录过大，请先整理资产")
    ids = [asset.id for asset, _ in rows]
    versions = (await session.scalars(select(AssetVersion).where(AssetVersion.asset_id.in_(ids))
        .order_by(AssetVersion.version.desc(), AssetVersion.id.desc()).limit(LIMIT+1))).all() if ids else []
    if len(versions) > LIMIT:
        raise ConflictError("声音版本目录过大，请先整理版本")
    by_asset = {}
    for version in versions:
        by_asset.setdefault(version.asset_id, []).append(version)
    asset_media = select(AssetVersion.media_file_id).where(AssetVersion.asset_id.in_(ids))
    linked_media = select(ProjectMediaLink.media_file_id).where(ProjectMediaLink.project_id == project.id)
    media = (await session.scalars(select(MediaFile).where(MediaFile.kind == "audio",
        MediaFile.purpose.not_in(["style", "builtin_style"]), owner_scope(MediaFile.owner_id, project.owner_id),
        or_(MediaFile.project_id == project.id, MediaFile.id.in_(linked_media), MediaFile.id.in_(asset_media)))
        .order_by(MediaFile.id.desc()).limit(LIMIT+1))).all()
    if len(media) > LIMIT:
        raise ConflictError("项目音频目录过大，请先整理素材")
    media_by_id = {item.id:item for item in media}
    # A media link does not turn an unadopted asset candidate into an upload.
    attached = set((await session.scalars(select(AssetVersion.media_file_id).where(
        AssetVersion.media_file_id.in_(media_by_id)))).all()) if media_by_id else set()
    items = []
    for asset, link in rows:
        if link is not None and link.production_archived:
            continue
        data = (link.production_data or {}) if link is not None else {}
        profile = effective_profile(asset, data)
        usage = profile.get("audio_usage")
        if usage == "unclassified" and "audio_usage" not in (data.get("profile") or {}):
            usage = (asset.attributes or {}).get("audio_purpose", usage)
        category = CATEGORIES.get(usage, "未分类")
        candidates = [version for version in by_asset.get(asset.id, []) if version.review_status != "archived"]
        if "adoptions" in data:
            adopted = {choice.get("version_id") for choice in data.get("adoptions", [])}
            candidates = [version for version in candidates if version.id in adopted]
        else:
            candidates = [version for version in candidates if version.is_final][:1]
        for version in candidates:
            source = media_by_id.get(version.media_file_id)
            if source and (is_reference(source) or source.purpose == "model_test"):
                continue
            items.append({"id":f"asset:{asset.id}:{version.id}", "mediaId":version.media_file_id,
                "assetId":asset.id, "versionId":version.id, "assetRevision":link.production_revision if link else 0,
                "label":f"{asset.name} · V{version.version}" + (" · 用途待确认" if usage == "voice" and source and source.purpose == "creative" else ""),
                "category":PRODUCTION_CATEGORIES.get(source.purpose,category) if source else category,
                "audioPurpose":source.purpose if source and source.purpose != "creative" else "unclassified",
                "available":available(source), "duration":source.duration if source else None})
    for source in media:
        if source.id in attached or is_reference(source) or source.purpose == "model_test":
            continue
        items.append({"id":f"media:{source.id}", "mediaId":source.id, "label":source.original_name or f"音频 {source.id}",
            "category":"未分类", "available":available(source), "duration":source.duration})
    return {"items":items}
