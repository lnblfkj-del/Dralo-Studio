"""C4-B deterministic private frame ingestion and local FFmpeg previsualization."""

# ruff: noqa: RUF001
import asyncio
import json
import struct
import tempfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.core.storage_safety import StorageUnavailableError, copy_storage_file, finish_storage_io, write_storage_bytes
from app.models import CanvasEdge, CanvasNode, Job, MediaFile, Project, ProjectMediaLink
from app.services import canvas_director_service as director
from app.services import canvas_generation_service as generation
from app.services import canvas_processing_service as processing
from app.services import canvas_service, job_service

MAX_BYTES = 100 * 1024 * 1024


def unpack(raw, directory=None):
    """Strict bounded binary framing: no archive names, paths or decompression bombs."""
    try:
        if not 12 < len(raw) <= MAX_BYTES or raw[:8] != b"WORKS3D1":
            raise ValueError("header")
        length = struct.unpack_from("<I", raw, 8)[0]
        if not 0 < length <= 8192:
            raise ValueError("metadata")
        meta = json.loads(raw[12 : 12 + length])
        if set(meta) != {"width", "height", "fps", "count", "sizes"}:
            raise ValueError("fields")
        w, h, fps, count = (meta[k] for k in ("width", "height", "fps", "count"))
        if any(type(v) is not int for v in (w, h, fps, count)):
            raise ValueError("integers")
        if fps not in (24, 30) or not 24 <= count <= fps * 15:
            raise ValueError("duration")
        if not 16 <= min(w, h) <= max(w, h) <= 640 or w % 2 or h % 2:
            raise ValueError("dimensions")
        sizes = meta["sizes"]
        if not isinstance(sizes, list) or len(sizes) != count:
            raise ValueError("count")
        if any(type(n) is not int or not 0 < n <= 2 * 1024 * 1024 for n in sizes):
            raise ValueError("frame size")
        offset = 12 + length
        if offset + sum(sizes) != len(raw):
            raise ValueError("truncated or trailing data")
        for index, size in enumerate(sizes):
            data = raw[offset : offset + size]
            with Image.open(BytesIO(data)) as image:
                if (
                    image.format != "PNG"
                    or image.size != (w, h)
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("PNG dimensions")
                image.load()
            if directory:
                # All names are generated here, never read from the client.
                write_storage_bytes(settings, Path(directory) / f"{index:05d}.png", data)
            offset += size
        return {k: v for k, v in meta.items() if k != "sizes"}
    except StorageUnavailableError:
        raise
    except Exception as exc:
        raise ConflictError(
            "预演帧无效：仅支持 24/30 fps、最长 15 秒、长边 640px 的完整 PNG 序列"
        ) from exc


async def find_request(session, project, key, request_id):
    await director.node_for(session, project, key)
    jobs = (
        await session.scalars(
            select(Job).where(
                Job.project_id == project.id,
                Job.owner_id == project.owner_id,
                Job.job_type == "media_process",
            )
        )
    ).all()
    return next(
        (
            j
            for j in jobs
            if j.payload.get("director_preview", {}).get("node_key") == key
            and j.payload.get("parameters", {}).get("canvas_request_id") == request_id
        ),
        None,
    )


async def abandon(session, project, key, request_id):
    # Serializes with submission: either cancel the existing job or reject any late upload.
    await generation.submission_lock(session, project)
    node = await director.node_for(session, project, key)
    job = await find_request(session, project, key, request_id)
    if job:
        if job.status not in job_service.TERMINAL_STATUSES:
            await job_service.cancel_job(session, job)
    else:
        record = director.record(node)
        cancelled = list(record.get("preview_cancellations", []))
        if request_id not in cancelled:
            if len(cancelled) >= 500:
                raise ConflictError("预演取消记录已达上限，请联系管理员")
            cancelled.append(request_id)
        node.data = {
            **node.data,
            "director_document": {**record, "preview_cancellations": cancelled},
        }
    await session.commit()
    return job


async def submit(session, project, key, request_id, expected_revision, raw):
    processing.executables()
    await generation.submission_lock(session, project)
    node = await director.node_for(session, project, key, write=True)
    if request_id in director.record(node).get("preview_cancellations", []):
        raise ConflictError("该预演请求已取消，迟到的数据不会生成视频")
    digest = generation.fingerprint(
        ["director_preview", key, expected_revision, sha256(raw).hexdigest()]
    )
    prior = await generation.existing_request(session, project, request_id, digest)
    if prior:
        return prior
    record = director.record(node)
    if record["revision"] != expected_revision or not record["state"]:
        raise ConflictError("3D 工程已改变，请保存并重新采集预演")
    meta = await asyncio.to_thread(unpack, raw)
    timeline = record["state"]["project"].get("timeline")
    if (
        not timeline
        or meta["fps"] != timeline["fps"]
        or meta["count"] != timeline["durationFrames"]
    ):
        raise ConflictError("预演帧率或时长与已保存工程不一致")
    if not any(len(t["keys"]) > 1 for t in timeline["tracks"]):
        raise ConflictError("请先设置动作或运镜关键帧")
    relative = Path("projects") / str(project.id) / "director" / (uuid4().hex + ".frames")
    path = settings.storage_path / relative
    try:
        await finish_storage_io(write_storage_bytes, settings, path, raw)
        x, y = await director.capture_position(session, node)
        output = CanvasNode(
            canvas_id=node.canvas_id,
            node_key=uuid4().hex,
            node_type="video",
            x=x,
            y=y,
            width=400,
            data={
                "title": "3D 动态预演",
                "director_origin": {
                    "node_key": key,
                    "revision": expected_revision,
                    "state": record["state"],
                },
            },
        )
        session.add(output)
        await session.flush()
        job = Job(
            owner_id=project.owner_id,
            project_id=project.id,
            job_type="media_process",
            target_type="canvas_node",
            target_id=output.id,
            provider="本地 3D 预演",
            model="FFmpeg",
            cost_estimate=0,
            max_attempts=1,
            payload={
                "director_preview": {
                    "node_key": key,
                    "revision": expected_revision,
                    "path": relative.as_posix(),
                    "hash": sha256(raw).hexdigest(),
                    **meta,
                },
                "origin_node_id": node.id,
                "output_kind": "video",
                "parameters": {
                    "source_node_key": output.node_key,
                    "canvas_request_id": request_id,
                    "canvas_request_digest": digest,
                },
            },
        )
        session.add(job)
        await session.flush()
        output.data = {**output.data, "job_id": job.id, "generation_status": "queued"}
        session.add(
            CanvasEdge(
                canvas_id=node.canvas_id,
                edge_key=uuid4().hex,
                source_key=key,
                target_key=output.node_key,
                data={"relation": "director_preview", "job_id": job.id},
            )
        )
        await session.commit()
        return job
    except Exception:
        await session.rollback()
        path.unlink(missing_ok=True)
        raise


async def preflight(session, job, retry=False):
    project = await session.get(Project, job.project_id)
    node = await session.get(CanvasNode, job.target_id)
    if not project or not node or project.owner_id != job.owner_id or node.node_type != "video":
        raise NotFoundError("预演项目或视频节点已删除")
    await canvas_service.validate_job_node(session, job, node)
    await canvas_service.assert_node_unlocked(session, project, node)
    p = job.payload["director_preview"]
    origin = await director.node_for(session, project, p["node_key"], write=True)
    if (
        origin.id != job.payload["origin_node_id"]
        or director.record(origin)["revision"] != p["revision"]
    ):
        raise ConflictError("源导演台已修改，旧预演结果不会回写，请重新提交")
    if retry:
        await processing.ensure_idle(session, project, node, job.id)
    root = settings.storage_path.resolve()
    path = (root / p["path"]).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or not 0 < path.stat().st_size <= MAX_BYTES
    ):
        raise NotFoundError("预演源帧已不可用，请重新采集")
    if await asyncio.to_thread(processing.digest_file, path) != p["hash"]:
        raise ConflictError("预演源帧校验失败")
    return node, path, p


async def execute(job_id, worker_id):
    final_path, committed = None, False

    async def active():
        async with SessionLocal() as session:
            ok = await job_service.renew_lease(session, job_id, worker_id)
            await session.commit()
            return ok

    try:
        async with SessionLocal() as session:
            if not await job_service.mark_processing(session, job_id, worker_id):
                return
            job = await session.get(Job, job_id)
            _, path, meta = await preflight(session, job)
            await session.commit()
        ffmpeg, _ = processing.executables()
        with tempfile.TemporaryDirectory(
            prefix=".director-preview-", dir=settings.storage_path
        ) as directory:
            raw = await asyncio.to_thread(path.read_bytes)
            await asyncio.to_thread(unpack, raw, directory)
            output = Path(directory) / "preview.mp4"
            await processing.command(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-framerate",
                    str(meta["fps"]),
                    "-i",
                    str(Path(directory) / "%05d.png"),
                    "-frames:v",
                    str(meta["count"]),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                timeout=settings.media_process_timeout_seconds,
                active=active,
                output=output,
            )
            result_meta = await processing.probe(output, active)
            if (result_meta["width"], result_meta["height"]) != (
                meta["width"],
                meta["height"],
            ) or abs(result_meta["duration"] - meta["count"] / meta["fps"]) > 0.05:
                raise ConflictError("预演产物尺寸或时长不符，结果未采用")
            # Verify encoded frame count and frame rate, not only the container duration.
            _, ffprobe = processing.executables()
            encoded = json.loads(
                await processing.command(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=nb_frames,r_frame_rate",
                        "-of",
                        "json",
                        str(output),
                    ],
                    timeout=20,
                    active=active,
                )
            )["streams"][0]
            if (
                int(encoded["nb_frames"]) != meta["count"]
                or encoded["r_frame_rate"] != f"{meta['fps']}/1"
            ):
                raise ConflictError("预演帧数或帧率校验失败")
            async with SessionLocal() as session:
                if not await job_service.renew_lease(session, job_id, worker_id):
                    return
                job = await session.get(Job, job_id)
                node, _, _ = await preflight(session, job)
                relative = (
                    Path("projects") / str(job.project_id) / "director" / (uuid4().hex + ".mp4")
                )
                final_path = settings.storage_path / relative
                await finish_storage_io(copy_storage_file, settings, output, final_path)
                artifact = MediaFile(
                    owner_id=job.owner_id,
                    project_id=job.project_id,
                    kind="video",
                    source="processing",
                    file_path=relative.as_posix(),
                    original_name="3D 动态预演.mp4",
                    mime_type="video/mp4",
                    size=final_path.stat().st_size,
                    hash=await asyncio.to_thread(processing.digest_file, final_path),
                    width=meta["width"],
                    height=meta["height"],
                    duration=result_meta["duration"],
                )
                session.info["media_quota_consuming_job"] = job_id
                session.add(artifact)
                await session.flush()
                session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=artifact.id))
                await canvas_service.record_job_media_version(session, job, node, artifact.id)
                node.data = {**node.data, "generation_status": "succeeded"}
                result = {
                    "media_file_id": artifact.id,
                    "canvas_node_id": node.node_key,
                    "kind": "video",
                    "fps": meta["fps"],
                    "frame_count": meta["count"],
                    "director_revision": meta["revision"],
                    **result_meta,
                }
                if not await job_service.mark_succeeded(session, job.id, worker_id, result):
                    raise ConflictError("预演已取消或租约失效，结果未采用")
                await session.commit()
                committed = True
    finally:
        if final_path and not committed:
            final_path.unlink(missing_ok=True)
