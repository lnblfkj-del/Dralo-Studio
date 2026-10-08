"""Adopt generated images by usage without changing frozen downstream inputs."""

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import AssetVersion, ProjectAssetLink


def adoption_key(asset_type, view_type, payload):
    explicit = payload.get("asset_adoption_key")
    if explicit:
        return explicit
    if view_type in {"first_frame", "last_frame", "key_frame", "storyboard_frame"}:
        return view_type
    if view_type not in {"base", "appearance"}:
        return f"{view_type}:{payload.get('view_label') or 'default'}"
    return {"costume": "appearance:default", "scene": "environment:default"}.get(asset_type, "default")


async def adopt_generated(session, job, asset, version, media):
    if job.project_id is None or version.view_type == "layout_sheet" or any(
        tag.startswith("qa:landscape-orientation-mismatch") for tag in version.tags or []
    ):
        return "candidate"
    link = await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == job.project_id, ProjectAssetLink.asset_id == asset.id,
    ).with_for_update().execution_options(populate_existing=True))
    if link is None or link.production_archived:
        return "archived"
    key = adoption_key(asset.asset_type, version.view_type, job.payload or {})
    data = dict(link.production_data or {})
    choices = list(data.get("adoptions") or [])
    current = next((row for row in choices if row.get("key") == key), None)
    old_version = await session.get(AssetVersion, current["version_id"]) if current else None
    # Generation request order, not arrival order, determines the latest result.
    if old_version and old_version.source_job_id and old_version.source_job_id > job.id:
        return "older_result"
    choices = [row for row in choices if row.get("key") != key]
    choices.append({"key": key, "version_id": version.id, "media_file_id": media.id,
                    "kind": "image", "origin": "explicit"})
    revision = link.production_revision
    written = await session.execute(update(ProjectAssetLink).where(
        ProjectAssetLink.id == link.id, ProjectAssetLink.production_revision == revision,
    ).values(production_data={**data, "adoptions": choices}, production_revision=revision + 1)
      .execution_options(synchronize_session=False))
    if written.rowcount != 1:
        raise ConflictError("资产采用资料并发变化，请刷新并检查任务结果，勿重复调用模型")
    version.review_status = "approved"
    if version.view_type in {"base", "appearance"}:
        await session.execute(update(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.view_type == version.view_type,
            AssetVersion.id != version.id,
        ).values(is_final=False).execution_options(synchronize_session=False))
        version.is_final = True
    return "adopted"
