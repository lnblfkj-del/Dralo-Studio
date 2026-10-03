"""Atomic local splitting of protected layout sheets into asset candidate versions."""

import asyncio
import tempfile
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.core.storage_safety import copy_storage_file, finish_storage_io
from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, AssetVersion, Job, MediaFile, Project, ProjectMediaLink
from app.schemas.asset import AssetSplitRequest
from app.schemas.canvas_processing import Crop
from app.services import asset_service, canvas_generation_service, canvas_processing_service, job_service
from app.services.team_access import same_team


def source_token(asset: Asset, version: AssetVersion, media: MediaFile) -> str:
    value = ":".join(
        str(item)
        for item in (
            asset.id,
            version.id,
            version.view_type,
            media.id,
            media.hash or "",
            media.width or 0,
            media.height or 0,
        )
    )
    return sha256(value.encode()).hexdigest()


async def source(
    session, project: Project, asset_id: int, version_id: int
) -> tuple[Asset, AssetVersion, MediaFile, Path]:
    asset = await asset_service.get_asset(session, project.id, asset_id)
    if asset.project_id != project.id:
        raise ConflictError("复用资产请先建立项目内变体，再拆分为项目版本")
    version = await session.scalar(
        select(AssetVersion).where(
            AssetVersion.id == version_id, AssetVersion.asset_id == asset.id
        )
    )
    if version is None:
        raise NotFoundError("排版预览版本不存在")
    if version.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE:
        raise ConflictError("只有待拆分的排版预览可以执行此操作")
    media = await session.get(MediaFile, version.media_file_id)
    if (
        media is None
        or media.kind != "image"
        or not await same_team(session, project.owner_id, media.owner_id)
    ):
        raise NotFoundError("排版预览图片不存在或无权访问")
    return asset, version, media, canvas_processing_service.file_path(media)


async def info(session, project: Project, asset_id: int, version_id: int) -> dict:
    asset, version, media, path = await source(session, project, asset_id, version_id)
    metadata = await canvas_processing_service.probe(path)
    if media.width != metadata["width"] or media.height != metadata["height"]:
        media.width, media.height = metadata["width"], metadata["height"]
    allowed = sorted(
        asset_service.ASSET_VIEW_TYPES.get(asset.asset_type, {"base"})
        - {asset_service.LAYOUT_SHEET_VIEW_TYPE}
    )
    return {
        "asset_id": asset.id,
        "asset_type": asset.asset_type,
        "version_id": version.id,
        "media_id": media.id,
        "width": metadata["width"],
        "height": metadata["height"],
        "source_token": source_token(asset, version, media),
        "allowed_view_types": allowed,
    }


async def submit(
    session, project: Project, asset_id: int, version_id: int, payload: AssetSplitRequest
) -> Job:
    await canvas_generation_service.submission_lock(session, project)
    digest = canvas_generation_service.fingerprint(
        ["asset_split", asset_id, version_id, payload.model_dump(mode="json")]
    )
    prior = await canvas_generation_service.existing_request(
        session, project, payload.request_id, digest
    )
    if prior:
        return prior
    asset, version, media, path = await source(session, project, asset_id, version_id)
    if payload.source_token != source_token(asset, version, media):
        raise ConflictError("排版预览或源文件已变化，请重新打开拆分工具")
    metadata = await canvas_processing_service.probe(path)
    normalized_regions = []
    for region in payload.regions:
        canvas_processing_service.validate(
            "image",
            Crop(
                kind="crop", x=region.x, y=region.y,
                width=region.width, height=region.height,
            ),
            metadata,
        )
        view_type, view_label = asset_service.normalize_asset_view(
            asset.asset_type, region.view_type, region.label
        )
        if view_type == asset_service.LAYOUT_SHEET_VIEW_TYPE:
            raise ConflictError("拆分结果不能继续标记为排版预览")
        normalized_regions.append(
            {**region.model_dump(mode="json"), "view_type": view_type, "label": view_label}
        )
    job = Job(
        owner_id=project.owner_id,
        project_id=project.id,
        job_type=canvas_processing_service.JOB_TYPE,
        target_type="asset",
        target_id=asset.id,
        provider="本地媒体处理",
        model="FFmpeg · 资产排版拆分",
        cost_estimate=0,
        max_attempts=1,
        payload={
            "asset_split": {
                "asset_id": asset.id,
                "source_version_id": version.id,
                "source_media_id": media.id,
                "source_token": payload.source_token,
                "regions": normalized_regions,
            },
            "source_hash": await asyncio.to_thread(canvas_processing_service.digest_file, path),
            "parameters": {
                "canvas_request_id": payload.request_id,
                "canvas_request_digest": digest,
            },
        },
    )
    session.add(job)
    await session.flush()
    return job


async def preflight(session, job: Job, retry: bool = False):
    project = await session.get(Project, job.project_id)
    split = (job.payload or {}).get("asset_split") or {}
    if project is None or project.owner_id != job.owner_id:
        raise NotFoundError("拆分项目不存在")
    asset_id = split.get("asset_id")
    version_id = split.get("source_version_id")
    if not isinstance(asset_id, int) or not isinstance(version_id, int):
        raise ConflictError("拆分任务缺少有效的源资产版本")
    asset, version, media, path = await source(
        session, project, asset_id, version_id
    )
    if (
        media.id != split.get("source_media_id")
        or source_token(asset, version, media) != split.get("source_token")
        or await asyncio.to_thread(canvas_processing_service.digest_file, path)
        != job.payload.get("source_hash")
    ):
        raise ConflictError("排版预览已变化，旧拆分请求不再写入")
    for region in split.get("regions", []):
        asset_service.normalize_asset_view(asset.asset_type, region.get("view_type"), region.get("label"))
    return project, asset, version, media, path, split


async def execute(job_id: int, worker_id: str) -> None:
    final_paths: list[Path] = []
    committed = False

    async def active():
        async with SessionLocal() as session:
            live = await job_service.renew_lease(session, job_id, worker_id)
            await session.commit()
            return live

    try:
        async with SessionLocal() as session:
            if not await job_service.mark_processing(session, job_id, worker_id):
                return
            job = await session.get(Job, job_id)
            _, _, _, _, path, split = await preflight(session, job)
            await session.commit()
        metadata = await canvas_processing_service.probe(path, active)
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".asset-views-", dir=settings.storage_path) as directory:
            outputs = []
            for index, region in enumerate(split["regions"]):
                operation = Crop(
                    kind="crop", x=region["x"], y=region["y"],
                    width=region["width"], height=region["height"],
                )
                canvas_processing_service.validate("image", operation, metadata)
                output = Path(directory) / f"{index}.png"
                await canvas_processing_service.command(
                    canvas_processing_service.build_command(path, output, "image", operation),
                    timeout=settings.media_process_timeout_seconds,
                    active=active,
                    output=output,
                )
                result = await canvas_processing_service.probe(output, active)
                if (result["width"], result["height"]) != (region["width"], region["height"]):
                    raise ConflictError("拆分尺寸与确认区域不符")
                outputs.append((output, result, await asyncio.to_thread(canvas_processing_service.digest_file, output)))
            async with SessionLocal() as session:
                if not await job_service.renew_lease(session, job_id, worker_id):
                    return
                job = await session.get(Job, job_id)
                project, asset, source_version, _, _, split = await preflight(session, job)
                next_version = int(
                    await session.scalar(
                        select(func.coalesce(func.max(AssetVersion.version), 0)).where(
                            AssetVersion.asset_id == asset.id
                        )
                    )
                )
                artifacts = []
                for region, (output, result, digest) in zip(split["regions"], outputs, strict=True):
                    relative = (
                        Path("projects")
                        / str(project.id)
                        / "assets"
                        / str(asset.id)
                        / (uuid4().hex + ".png")
                    )
                    destination = settings.storage_path / relative
                    final_paths.append(destination)
                    await finish_storage_io(copy_storage_file, settings, output, destination)
                    media = MediaFile(
                        owner_id=job.owner_id, project_id=project.id, kind="image",
                        source="processing", file_path=relative.as_posix(),
                        original_name=f"{asset.name}-{region['label']}.png", mime_type="image/png",
                        size=destination.stat().st_size, hash=digest,
                        width=result["width"], height=result["height"],
                    )
                    session.info["media_quota_consuming_job"] = job_id
                    session.add(media)
                    await session.flush()
                    session.add(ProjectMediaLink(project_id=project.id, media_file_id=media.id))
                    next_version += 1
                    version = AssetVersion(
                        asset_id=asset.id, media_file_id=media.id, source_job_id=job.id,
                        version=next_version, prompt=source_version.prompt,
                        parameters={
                            "source": "layout-sheet-split",
                            "source_version_id": source_version.id,
                            "source_media_id": source_version.media_file_id,
                            "split_request_id": job.payload["parameters"]["canvas_request_id"],
                            "region": {key: region[key] for key in ("x", "y", "width", "height")},
                        },
                        view_label=region["label"], view_type=region["view_type"],
                        review_status="candidate", tags=["排版拆分"], is_final=False,
                    )
                    session.add(version)
                    await session.flush()
                    artifacts.append(
                        {
                            "asset_version_id": version.id,
                            "media_file_id": media.id,
                            "view_type": version.view_type,
                            "view_label": version.view_label,
                            "width": media.width,
                            "height": media.height,
                        }
                    )
                if not await job_service.mark_succeeded(
                    session,
                    job.id,
                    worker_id,
                    {
                        "asset_id": asset.id,
                        "source_version_id": source_version.id,
                        "operation": "split_layout_sheet",
                        "outputs": artifacts,
                    },
                ):
                    raise ConflictError("任务已取消，拆分结果未写入资产")
                await session.commit()
                committed = True
    finally:
        if not committed:
            for path in final_paths:
                path.unlink(missing_ok=True)
