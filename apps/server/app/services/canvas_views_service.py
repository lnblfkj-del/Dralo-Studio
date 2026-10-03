"""Explicit, lossless composite splitting; all children commit together, never bind entities."""

# ruff: noqa: RUF001
import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.storage_safety import copy_storage_file, finish_storage_io
from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.models import CanvasEdge, CanvasNode, Job, MediaFile, Project, ProjectMediaLink
from app.schemas.canvas_processing import Crop, ProcessMedia
from app.services import canvas_advanced_service as advanced
from app.services import canvas_generation_service as generation
from app.services import canvas_processing_service as processing
from app.services import canvas_service, job_service


async def source(session, project, key):
    node, media, token = await advanced.source(session, project, key)
    await canvas_service.assert_node_unlocked(session, project, node)
    if node.node_type not in {"image", "character", "scene", "costume", "prop"} or not media or media.kind != "image":
        raise ConflictError("请先选择图片或给共享视觉资产绑定主视图")
    return node, media, token, processing.file_path(media)


async def info(session, project, key):
    _, media, token, path = await source(session, project, key)
    metadata = await processing.probe(path)
    processing.validate("image", Crop(kind="crop", x=0, y=0, width=2, height=2), metadata)
    return {**metadata, "media_id": media.id, "source_token": token}


async def submit(session, project, key, payload):
    await generation.submission_lock(session, project)
    digest = generation.fingerprint([key, payload.source_media_id, payload.operation.model_dump()])
    prior = await generation.existing_request(session, project, payload.request_id, digest)
    if prior:
        return prior
    origin, media, token, path = await source(session, project, key)
    if media.id != payload.source_media_id or token != payload.operation.source_token:
        raise ConflictError("来源图片或实体已改变，请重新打开拆分工具")
    await processing.ensure_idle(session, project, origin)
    metadata = await processing.probe(path)
    for region in payload.operation.regions:
        processing.validate("image", region, metadata)
    source_hash = await asyncio.to_thread(processing.digest_file, path)
    await canvas_service.guard_media_revision(session, project, payload.expected_revision)
    x, y, parent = origin.x, origin.y, origin.parent_key
    while parent:
        group = await canvas_service.get_canvas_node(session, project.id, parent)
        x, y, parent = x + group.x, y + group.y, group.parent_key
    x += (origin.width or 430) + 80
    occupied = list(
        (
            await session.scalars(
                select(CanvasNode).where(
                    CanvasNode.canvas_id == origin.canvas_id, CanvasNode.parent_key.is_(None)
                )
            )
        ).all()
    )
    targets = []
    for index, region in enumerate(payload.operation.regions):
        tx, ty = x + index % 3 * 510, y + index // 3 * 720
        while any(
            tx < n.x + (n.width or 430) + 40
            and tx + 470 > n.x
            and ty < n.y + (n.height or 650) + 40
            and ty + 690 > n.y
            for n in occupied
        ):
            ty += 720
        node = CanvasNode(
            canvas_id=origin.canvas_id,
            node_key=uuid4().hex,
            node_type="image",
            x=tx,
            y=ty,
            width=430,
            data={"title": region.label, "processing_origin": key},
        )
        session.add(node)
        targets.append(node)
        occupied.append(node)
    await session.flush()
    job = Job(
        owner_id=project.owner_id,
        project_id=project.id,
        job_type=processing.JOB_TYPE,
        target_type="canvas_node",
        target_id=targets[0].id,
        provider="本地媒体处理",
        model="FFmpeg · 多视图拆分",
        cost_estimate=0,
        max_attempts=1,
        payload={
            "processing": payload.model_dump(),
            "origin_node_id": origin.id,
            "origin_key": key,
            "source_hash": source_hash,
            "target_ids": [n.id for n in targets],
            "target_keys": [n.node_key for n in targets],
            "parameters": {
                "source_node_key": targets[0].node_key,
                "canvas_request_id": payload.request_id,
                "canvas_request_digest": digest,
            },
        },
    )
    session.add(job)
    await session.flush()
    for node in targets:
        node.data = {**node.data, "job_id": job.id, "generation_status": "queued"}
        session.add(
            CanvasEdge(
                canvas_id=origin.canvas_id,
                edge_key=uuid4().hex,
                source_key=key,
                target_key=node.node_key,
                data={"processing_job_id": job.id},
            )
        )
    await session.flush()
    return job


async def preflight(session, job, retry=False):
    project = await session.get(Project, job.project_id)
    if not project or project.owner_id != job.owner_id:
        raise NotFoundError("原项目不存在")
    request = ProcessMedia.model_validate(job.payload["processing"])
    origin, media, token, path = await source(session, project, job.payload["origin_key"])
    if (
        origin.id != job.payload["origin_node_id"]
        or media.id != request.source_media_id
        or token != request.operation.source_token
    ):
        raise ConflictError("拆分来源或实体已变化，不回写旧请求")
    if await asyncio.to_thread(processing.digest_file, path) != job.payload["source_hash"]:
        raise ConflictError("原图片文件已改变")
    targets = []
    for target_id, target_key in zip(
        job.payload["target_ids"], job.payload["target_keys"], strict=True
    ):
        node = await session.get(CanvasNode, target_id)
        if (
            not node
            or node.node_key != target_key
            or node.canvas_id != origin.canvas_id
            or node.node_type != "image"
        ):
            raise ConflictError("拆分目标已删除或改变类型")
        await canvas_service.assert_node_unlocked(session, project, node)
        if node.data.get("job_id") != job.id or node.data.get("media_id"):
            raise ConflictError("拆分目标已由其他操作修改，不覆盖现有素材")
        if retry:
            await processing.ensure_idle(session, project, node, job.id)
        targets.append(node)
    if len(targets) != len(request.operation.regions):
        raise ConflictError("拆分目标数量不一致")
    return targets, media, path, request


async def execute(job_id, worker_id):
    final_paths, committed = [], False

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
            _, _, path, request = await preflight(session, job)
            await session.commit()
        metadata = await processing.probe(path, active)
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".views-", dir=settings.storage_path) as directory:
            outputs = []
            for index, region in enumerate(request.operation.regions):
                processing.validate("image", region, metadata)
                output = Path(directory) / f"{index}.png"
                await processing.command(
                    processing.build_command(path, output, "image", region),
                    timeout=settings.media_process_timeout_seconds,
                    active=active,
                    output=output,
                )
                if not output.is_file() or not 0 < output.stat().st_size < processing.MAX_BYTES:
                    raise ConflictError("拆分结果为空或过大")
                result = await processing.probe(output, active)
                if (result["width"], result["height"]) != (region.width, region.height):
                    raise ConflictError("拆分尺寸与确认区域不符")
                outputs.append((output, await asyncio.to_thread(processing.digest_file, output)))
            async with SessionLocal() as session:
                if not await job_service.renew_lease(session, job_id, worker_id):
                    return
                job = await session.get(Job, job_id)
                targets, _, _, _ = await preflight(session, job)
                artifacts = []
                for node, region, (output, digest) in zip(
                    targets, request.operation.regions, outputs, strict=True
                ):
                    relative = (
                        Path("projects")
                        / str(job.project_id)
                        / "processed"
                        / (uuid4().hex + ".png")
                    )
                    destination = settings.storage_path / relative
                    final_paths.append(destination)
                    # A rename retains TemporaryDirectory's restrictive Windows ACL.
                    # Create the permanent file under the storage directory's ACL instead.
                    await finish_storage_io(copy_storage_file, settings, output, destination)
                    media = MediaFile(
                        owner_id=job.owner_id,
                        project_id=job.project_id,
                        kind="image",
                        source="processing",
                        file_path=relative.as_posix(),
                        original_name=f"view-{request.source_media_id}-{node.node_key}.png",
                        mime_type="image/png",
                        size=destination.stat().st_size,
                        hash=digest,
                        width=region.width,
                        height=region.height,
                    )
                    session.info["media_quota_consuming_job"] = job_id
                    session.add(media)
                    await session.flush()
                    session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
                    # The shared job targets its first child; each child has its own version.
                    provenance = {
                        "media_id": media.id,
                        "job_id": job.id,
                        "source_media_id": request.source_media_id,
                        "operation": {"kind": "split_views", "region": region.model_dump()},
                    }
                    node.data = {
                        **node.data,
                        "media_id": media.id,
                        "media_versions": [provenance],
                        "generation_status": "succeeded",
                    }
                    artifacts.append(
                        {
                            "media_file_id": media.id,
                            "canvas_node_id": node.node_key,
                            "label": region.label,
                            "width": region.width,
                            "height": region.height,
                        }
                    )
                if not await job_service.mark_succeeded(
                    session,
                    job.id,
                    worker_id,
                    {
                        "kind": "image",
                        "operation": "split_views",
                        "source_media_id": request.source_media_id,
                        "outputs": artifacts,
                        "canvas_node_id": targets[0].node_key,
                    },
                ):
                    raise ConflictError("任务已取消，拆分结果未采用")
                await session.commit()
                committed = True
    finally:
        if not committed:
            for path in final_paths:
                path.unlink(missing_ok=True)
