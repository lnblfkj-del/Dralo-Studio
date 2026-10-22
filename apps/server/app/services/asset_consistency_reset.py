"""Explicit reset of selected visual assets without removing source cards."""

# ruff: noqa: RUF001

from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models import AssetVersion, Job, MediaFile, ProjectAssetLink
from app.services.asset_consistency_service import lock_project, project_rows
from app.services.asset_prompt_state import ACTIVE


async def reset_generated(session, project_id, asset_ids):
    await lock_project(session, project_id)
    ids = set(asset_ids)
    rows = [row for row in await project_rows(session, project_id) if row["asset_id"] in ids]
    if len(rows) != len(ids):
        raise ValidationError("清理范围包含不属于本项目的视觉资产或已归档资产")
    active = await session.scalar(select(Job.id).where(
        Job.project_id == project_id, Job.status.in_(ACTIVE),
        Job.target_type.in_(["asset", "asset_prompt_proposal", "asset_prompt_batch", "asset_image_batch"]),
    ).limit(1))
    if active:
        raise ConflictError("项目仍有资产生成任务，请完成或取消后再清理")
    shared = await session.scalar(select(ProjectAssetLink.asset_id).where(
        ProjectAssetLink.asset_id.in_(ids), ProjectAssetLink.project_id != project_id,
    ).limit(1))
    if shared:
        raise ConflictError("所选资产已被其他项目复用，请先移除共享资产后清理")
    versions = (await session.scalars(select(AssetVersion).join(
        MediaFile, MediaFile.id == AssetVersion.media_file_id,
    ).where(AssetVersion.asset_id.in_(ids), MediaFile.kind == "image", MediaFile.source == "generation",
            AssetVersion.review_status != "archived"))).all()
    removed = {version.id for version in versions}
    for version in versions:
        version.review_status = "archived"
        version.is_final = False
        version.tags = [*(version.tags or []), "consistency:reset"]
    for row in rows:
        link = row["link"]
        data = dict(link.production_data or {})
        data["prompt_anchor"] = ""
        data.pop("prompt_optimization", None)
        data.pop("consistency_design", None)
        data.pop("consistency_design_job_id", None)
        data.pop("consistency_image_basis", None)
        data.pop("field_trust_prompt_override", None)
        data["adoptions"] = [choice for choice in data.get("adoptions", []) if choice.get("version_id") not in removed]
        link.production_data = data
        link.production_revision += 1
    await session.flush()
    return {"asset_count": len(rows), "image_count": len(versions)}
