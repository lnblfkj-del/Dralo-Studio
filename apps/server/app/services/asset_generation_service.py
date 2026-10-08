"""Persist completed image-generation jobs and their asset versions."""

from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.media_quota import require_media_budget
from app.core.storage_safety import write_storage_bytes
from app.models import Asset, AssetVersion, Job, MediaFile, ProjectMediaLink
from app.services.asset_reference_service import image_format


async def finalize_image_job(
    session: AsyncSession, job: Job, result: dict[str, object]
) -> dict[str, Any]:
    from app.services import asset_service
    if job.target_type == "style_image":
        from io import BytesIO

        from fastapi import UploadFile

        from app.services import media_service

        data = result.get("image_bytes")
        if not isinstance(data, bytes):
            raise ConflictError("风格图片任务没有返回有效文件")
        extension, mime_type = image_format(data)
        stream = UploadFile(file=BytesIO(data))
        try:
            media = await media_service.save_upload(
                session, owner_id=job.owner_id, stream=stream,
                filename=f"风格图-{job.id}.{extension}", content_type=mime_type, project=None,
            )
        finally:
            await stream.close()
        media.source = "generation"
        media.purpose = "style"
        if job.target_id is not None and job.payload.get("fill_missing_style_image") is True:
            from app.models.agent_config import StylePreset

            await session.flush()
            linked = await session.execute(
                update(StylePreset).where(
                    StylePreset.id == job.target_id,
                    StylePreset.preview_media_id.is_(None),
                    StylePreset.reference_media_id.is_(None),
                ).values(preview_media_id=media.id, reference_media_id=media.id)
            )
            return {"media_file_id": media.id, "media_url": f"/api/media/{media.id}",
                    "style_id": job.target_id, "style_linked": linked.rowcount == 1}
        return {"media_file_id": media.id, "media_url": f"/api/media/{media.id}"}
    if job.target_type != "asset" or job.target_id is None:
        raise ConflictError("图片任务缺少资产关联")
    asset = (
        await asset_service.get_asset(session, job.project_id, job.target_id)
        if job.project_id is not None
        else await asset_service.get_owned_global_asset(session, job.owner_id, job.target_id)
    )
    data = result.get("image_bytes")
    if not isinstance(data, bytes):
        raise ConflictError("图片任务没有返回有效文件")
    await session.execute(update(Asset).where(Asset.id == asset.id).values(updated_at=func.current_timestamp()))
    existing_version = await session.scalar(select(AssetVersion).where(
        AssetVersion.asset_id == asset.id, AssetVersion.source_job_id == job.id,
    ))
    if existing_version is not None:
        return {
            **(job.result or {}), "asset_id": asset.id,
            "asset_version_id": existing_version.id, "media_file_id": existing_version.media_file_id,
            "media_url": f"/api/media/{existing_version.media_file_id}",
            "version": existing_version.version, "already_persisted": True,
        }
    extension, mime_type = image_format(data)
    relative_path = (
        (Path("projects") / str(job.project_id) if job.project_id is not None else Path("users") / str(job.owner_id))
        / "assets"
        / str(asset.id)
        / f"{uuid4().hex}.{extension}"
    )
    absolute_path = settings.storage_path / relative_path
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(data)) as generated_image:
        generated_image.load()
        width, height = generated_image.size
    await require_media_budget(session, len(data))
    write_storage_bytes(settings, absolute_path, data)
    media = MediaFile(
        project_id=job.project_id,
        owner_id=job.owner_id,
        kind="image",
        source="generation",
        file_path=relative_path.as_posix(),
        original_name=absolute_path.name,
        mime_type=mime_type,
        size=len(data),
        width=width,
        height=height,
        hash=sha256(data).hexdigest(),
    )
    session.add(media)
    await session.flush()
    if job.project_id is not None:
        session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
        await session.flush()
    next_version = (
        int(
            await session.scalar(
                select(func.coalesce(func.max(AssetVersion.version), 0)).where(
                    AssetVersion.asset_id == asset.id
                )
            )
        )
        + 1
    )
    view_type, view_label = asset_service.normalize_asset_view(
        asset.asset_type,
        str(job.payload.get("view_type") or "") or None,
        job.payload.get("view_label"),
    )
    character_sheet = dict((job.payload or {}).get("character_reference_sheet") or {})
    version_tags = [
        str(value) for value in (job.payload or {}).get("version_tags", [])
        if isinstance(value, str) and value.strip()
    ]
    if character_sheet and width <= height:
        version_tags.append("qa:landscape-orientation-mismatch")
    requires_manual_review = bool((job.payload or {}).get("requires_manual_review"))
    version = AssetVersion(
        asset_id=asset.id,
        media_file_id=media.id,
        source_job_id=job.id,
        version=next_version,
        prompt=str(job.payload["prompt"]),
        negative_prompt=job.payload.get("negative_prompt"),
        parameters=job.payload.get("parameters", {}),
        view_label=view_label if next_version == 1 else (job.payload.get("view_label") or f"{view_label} {next_version}"),
        view_type=view_type,
        review_status=(
            "approved"
            if next_version == 1 and view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE and not requires_manual_review
            else "candidate"
        ),
        tags=list(dict.fromkeys(version_tags)),
        is_final=next_version == 1 and view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE and not requires_manual_review,
    )
    session.add(version)
    await session.flush()
    from app.services.asset_image_adoption import adopt_generated
    adoption_status = await adopt_generated(session, job, asset, version, media)
    frame_binding_status = None
    if isinstance((job.payload or {}).get("segment_frame"), dict):
        from app.services.segment_first_frame_service import bind_completed_frame

        frame_binding_status = await bind_completed_frame(
            session,
            job,
            asset_id=asset.id,
            asset_version_id=version.id,
            media_file_id=media.id,
        )
    return {
        "asset_id": asset.id,
        "asset_version_id": version.id,
        "media_file_id": media.id,
        "media_url": f"/api/media/{media.id}",
        "version": version.version,
        "revised_prompt": result.get("revised_prompt"),
        "character_reference_sheet": (
            {
                "contract": character_sheet.get("contract"),
                "view_key": character_sheet.get("view_key"),
                "requires_manual_review": requires_manual_review,
            }
            if character_sheet else None
        ),
        "segment_frame_binding_status": frame_binding_status,
        "adoption_status": adoption_status,
    }
