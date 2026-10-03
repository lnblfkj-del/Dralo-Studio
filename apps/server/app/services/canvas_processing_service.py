"""C3-A local FFmpeg jobs: private files, immutable inputs, transactional versions."""
# ruff: noqa: RUF001

import asyncio
import contextlib
import json
import math
import shutil
import subprocess
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from app.services.team_access import owner_scope, same_team

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.core.media_command_safety import local_media_arguments
from app.core.storage_safety import copy_storage_file, finish_storage_io, require_storage_capacity, storage_reservation
from app.models import CanvasEdge, CanvasNode, Job, MediaFile, Project, ProjectMediaLink
from app.schemas.canvas_processing import ProcessMedia
from app.services import canvas_generation_service, canvas_service, job_service

JOB_TYPE = "media_process"
MAX_BYTES = 500 * 1024 * 1024
FORMATS = "mov,matroska,webm,mp3,wav,ogg,flac,aac,image2,png_pipe,jpeg_pipe,webp_pipe,gif"
LABELS = {
    "crop": "裁剪",
    "rotate": "旋转",
    "trim": "截取",
    "frame": "抽帧",
    "extract_audio": "提取音轨",
    "mix_audio": "音画合成",
}


def executables():
    ffmpeg, ffprobe = shutil.which(settings.ffmpeg_path), shutil.which(settings.ffprobe_path)
    if not ffmpeg or not ffprobe:
        raise ConflictError("基础处理服务未就绪：请配置 FFMPEG_PATH 与 FFPROBE_PATH")
    return ffmpeg, ffprobe


def file_path(media):
    root = settings.storage_path.resolve()
    path = (root / media.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFoundError("源素材文件不存在")
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ConflictError("基础处理仅支持 0～500 MB 的素材")
    return path


async def command(args, *, timeout, active=None, output=None):
    with storage_reservation(settings, MAX_BYTES if output is not None else 0, target=output):
        return await _command(args, timeout=timeout, active=active, output=output)


async def _command(args, *, timeout, active=None, output=None):
    """No shell/stdin/network; bounded buffers; kill on cancellation or lease loss."""
    require_storage_capacity(settings, target=output)
    args = local_media_arguments(args)
    flags = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if hasattr(subprocess, "CREATE_NO_WINDOW")
        else {}
    )
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **flags,
    )

    async def read_limited(stream, limit):
        data = bytearray()
        while chunk := await stream.read(8192):
            data.extend(chunk)
            if len(data) > limit:
                del data[:-limit]
        return bytes(data)

    out = asyncio.create_task(read_limited(process.stdout, 1024 * 1024))
    err = asyncio.create_task(read_limited(process.stderr, 16384))
    started = time.monotonic()
    try:
        while process.returncode is None:
            require_storage_capacity(settings, target=output)
            if active and not await active():
                raise ConflictError("处理已取消或执行租约已失效")
            if time.monotonic() - started > timeout:
                raise ConflictError("媒体处理超时，请缩短素材后重试")
            if output and output.exists() and output.stat().st_size > MAX_BYTES:
                raise ConflictError("处理结果超过 500 MB 限制")
            await asyncio.sleep(0.2)
        stdout, _ = await asyncio.gather(out, err)
        if process.returncode:
            raise ConflictError("媒体无法解码或处理失败，请检查文件与处理参数")
        return stdout
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await process.wait()
        await asyncio.gather(out, err, return_exceptions=True)


async def probe(path, active=None):
    _, ffprobe = executables()
    raw = await command(
        [
            ffprobe,
            "-v",
            "error",
            "-max_alloc",
            "268435456",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            FORMATS,
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout=20,
        active=active,
    )
    try:
        data = json.loads(raw)
        streams = data.get("streams", [])
        visual = next(
            (
                s
                for s in streams
                if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")
            ),
            {},
        )
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        duration = float(
            data.get("format", {}).get("duration")
            or visual.get("duration")
            or audio.get("duration")
            or 0
        )
        width, height = int(visual.get("width", 0)), int(visual.get("height", 0))
        rotation = next(
            (s.get("rotation", 0) for s in visual.get("side_data_list", []) if "rotation" in s), 0
        )
        return {
            "width": width,
            "height": height,
            "duration": duration,
            "has_audio": bool(audio),
            "rotation": rotation,
            "format": data.get("format", {}).get("format_name", ""),
        }
    except (ValueError, TypeError, KeyError) as exc:
        raise ConflictError("无法读取媒体尺寸或时长") from exc


def validate(kind, operation, metadata):
    allowed = {"image": {"crop", "rotate"}, "video": set(LABELS), "audio": {"trim"}}
    op = operation.kind
    if op not in allowed.get(kind, set()):
        raise ConflictError("该素材类型不支持此处理工具")
    w, h, duration = metadata["width"], metadata["height"], metadata["duration"]
    if not math.isfinite(duration) or duration < 0 or duration > 1800:
        raise ConflictError("基础处理支持最长 30 分钟的素材")
    if kind in {"image", "video"} and (min(w, h) < 1 or max(w, h) > 8192 or w * h > 33_554_432):
        raise ConflictError("图片或视频尺寸无效，最大 8192 边长 / 3200 万像素")
    if kind == "image" and (metadata["format"] == "gif" or duration > 0):
        raise ConflictError("基础图片工具仅处理静态图片，动态素材请使用视频工具")
    if kind in {"audio", "video"} and duration <= 0:
        raise ConflictError("媒体时长无法确认，不能安全处理")
    if kind == "video" and metadata.get("rotation"):
        raise ConflictError(
            "此视频含旋转元数据，请先导入已规范方向的视频，避免预览与裁剪坐标不一致"
        )
    if kind == "audio" and not metadata["has_audio"]:
        raise ConflictError("素材不包含音轨")
    if op == "crop":
        if operation.x + operation.width > w or operation.y + operation.height > h:
            raise ConflictError("裁剪区域超出源素材边界")
        if kind == "video" and any(
            v % 2 for v in (operation.x, operation.y, operation.width, operation.height)
        ):
            raise ConflictError("视频裁剪坐标与尺寸须为偶数，以保持像素精度")
    if op == "trim" and operation.end > duration:
        raise ConflictError("截取结束时间超出素材时长")
    if op == "frame" and operation.at >= duration:
        raise ConflictError("抽帧时间必须小于视频时长")
    if op == "extract_audio" and not metadata["has_audio"]:
        raise ConflictError("此视频没有音轨")
    if op == "mix_audio" and operation.start + (operation.trim_end - operation.trim_start) > duration + 0.01:
        raise ConflictError("音轨放置范围超出视频时长")
    if kind == "video" and op in {"rotate", "trim"} and (w % 2 or h % 2):
        raise ConflictError("视频编码要求偶数尺寸，请先裁剪至偶数宽高")
    return "image" if op == "frame" else "audio" if op == "extract_audio" else kind


def build_command(source, output, kind, operation, audio_source=None, duration=None):
    ffmpeg, _ = executables()
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "268435456",
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        FORMATS,
        "-noautorotate",
        "-i",
        str(source),
    ]
    op = operation.kind
    if op == "mix_audio":
        if audio_source is None or duration is None:
            raise ConflictError("音画合成缺少已冻结的音频素材")
        args += ["-i", str(audio_source), "-filter_complex", (
            f"[1:a:0]atrim=start={operation.trim_start}:end={operation.trim_end},"
            f"asetpts=PTS-STARTPTS,volume={operation.volume},"
            f"adelay={round(operation.start * 1000)}:all=1,apad[aout]"
        ), "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-t", str(duration), "-movflags", "+faststart"]
        return [*args, "-map_metadata", "-1", "-sn", "-dn", "-threads", "2", "-fs", str(MAX_BYTES), str(output)]
    if op == "trim":
        args += ["-ss", str(operation.start), "-t", str(operation.end - operation.start)]
    elif op == "frame":
        args += ["-ss", str(operation.at)]
    if kind == "audio":
        args += ["-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
    else:
        args += ["-map", "0:v:0"]
        filters = []
        if op == "crop":
            filters.append(
                f"crop={operation.width}:{operation.height}:{operation.x}:{operation.y}:exact=1"
            )
        elif op == "rotate":
            filters.append(
                {90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}[
                    operation.degrees
                ]
            )
        if kind == "video":
            args += [
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
            ]
        else:
            args += ["-frames:v", "1", "-c:v", "png", "-update", "1"]
        if filters:
            args += ["-vf", ",".join(filters)]
    return [
        *args,
        "-map_metadata",
        "-1",
        "-sn",
        "-dn",
        "-threads",
        "2",
        "-fs",
        str(MAX_BYTES),
        str(output),
    ]


async def context(session, project, node_key, media_id):
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    media = await session.get(MediaFile, media_id) if media_id else None
    if not media or not await same_team(session, media.owner_id, project.owner_id) or media.kind != node.node_type:
        raise NotFoundError("源素材不存在或类型不匹配")
    if node.data.get("media_id") != media.id:
        raise ConflictError("节点当前素材已改变，请重新打开处理工具")
    return node, media, file_path(media)


async def info(session, project, node_key):
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    _, media, path = await context(session, project, node_key, node.data.get("media_id"))
    metadata = await probe(path)
    audio_tracks = []
    if media.kind == "video":
        edges = (await session.scalars(select(CanvasEdge).where(CanvasEdge.canvas_id == node.canvas_id, CanvasEdge.target_key == node.node_key))).all()
        for edge in edges:
            if (edge.data or {}).get("purpose") != "audio_track":
                continue
            source = await session.scalar(select(CanvasNode).where(CanvasNode.canvas_id == node.canvas_id, CanvasNode.node_key == edge.source_key))
            audio = await session.get(MediaFile, (source.data or {}).get("media_id")) if source else None
            if not source or source.node_type != "audio" or not audio or not await same_team(session, audio.owner_id, project.owner_id) or audio.kind != "audio":
                continue
            audio_meta = await probe(file_path(audio))
            audio_tracks.append({"media_id": audio.id, "node_id": source.node_key, "title": source.data.get("title", "音轨"), "duration": audio_meta["duration"]})
    return {
        **metadata,
        "media_id": media.id,
        "kind": media.kind,
        "max_bytes": MAX_BYTES,
        "max_duration": 1800,
        "audio_tracks": audio_tracks,
    }


async def audio_context(session, project, video_node, media_id):
    audio = await session.scalar(select(MediaFile).where(MediaFile.id == media_id, owner_scope(MediaFile.owner_id, project.owner_id), MediaFile.kind == "audio"))
    if not audio:
        raise NotFoundError("合成音轨不存在")
    edges = (await session.scalars(select(CanvasEdge).where(CanvasEdge.canvas_id == video_node.canvas_id, CanvasEdge.target_key == video_node.node_key))).all()
    valid = False
    for edge in edges:
        if (edge.data or {}).get("purpose") != "audio_track":
            continue
        source = await session.scalar(select(CanvasNode).where(CanvasNode.canvas_id == video_node.canvas_id, CanvasNode.node_key == edge.source_key))
        if source and source.node_type == "audio" and (source.data or {}).get("media_id") == audio.id:
            valid = True
            break
    if not valid:
        raise ConflictError("所选音轨未以“后期音轨”连接到当前视频节点")
    path = file_path(audio)
    metadata = await probe(path)
    return audio, path, metadata


async def ensure_idle(session, project, node, exclude=None):
    active = await session.scalar(
        select(Job.id).where(
            Job.project_id == project.id,
            Job.target_type == "canvas_node",
            Job.target_id == node.id,
            Job.status.not_in(job_service.TERMINAL_STATUSES),
            Job.id != (exclude or -1),
        )
    )
    if active:
        raise ConflictError("目标节点已有进行中的任务")


async def submit(session, project, node_key, payload):
    if payload.operation.kind == "split_views":
        from app.services import canvas_views_service

        return await canvas_views_service.submit(session, project, node_key, payload)
    await canvas_generation_service.submission_lock(session, project)
    digest = canvas_generation_service.fingerprint(
        [node_key, payload.source_media_id, payload.operation.model_dump()]
    )
    prior = await canvas_generation_service.existing_request(
        session, project, payload.request_id, digest
    )
    if prior:
        return prior
    node, media, path = await context(session, project, node_key, payload.source_media_id)
    await ensure_idle(session, project, node)
    metadata = await probe(path)
    kind = validate(media.kind, payload.operation, metadata)
    audio_hash = None
    director_context = None
    if payload.operation.kind == "mix_audio":
        _audio, audio_path, audio_metadata = await audio_context(session, project, node, payload.operation.audio_media_id)
        if payload.operation.trim_end > audio_metadata["duration"] + 0.01:
            raise ConflictError("音轨裁切结束时间超出音频时长")
        audio_hash = await asyncio.to_thread(digest_file, audio_path)
        source_version = next(
            (item for item in (node.data or {}).get("media_versions", [])
             if item.get("media_id") == media.id and item.get("job_id")),
            None,
        )
        if source_version:
            source_job = await session.get(Job, source_version["job_id"])
            compilation = (((source_job.payload or {}).get("parameters") or {}).get("video_compilation") if source_job else None)
            package = compilation.get("director_shot_package") if isinstance(compilation, dict) else None
            if isinstance(package, dict):
                director_context = {
                    "director_node_key": package.get("director_node_key"),
                    "director_revision": package.get("director_revision"),
                    "duration_seconds": package.get("duration_seconds"),
                    "package_fingerprint": package.get("package_fingerprint"),
                }
    await canvas_service.guard_media_revision(session, project, payload.expected_revision)
    origin = node
    if kind != media.kind:
        x, y, parent = node.x, node.y, node.parent_key
        while parent:
            p = await canvas_service.get_canvas_node(session, project.id, parent)
            x, y, parent = x + p.x, y + p.y, p.parent_key
        x += (origin.width or 430) + 80
        occupied = (
            await session.scalars(
                select(CanvasNode).where(
                    CanvasNode.canvas_id == origin.canvas_id, CanvasNode.parent_key.is_(None)
                )
            )
        ).all()
        while any(
            x < item.x + (item.width or 430) + 40
            and x + 430 + 40 > item.x
            and y < item.y + (item.height or 650) + 40
            and y + 650 + 40 > item.y
            for item in occupied
        ):
            y += 720
        node = CanvasNode(
            canvas_id=origin.canvas_id,
            node_key=uuid4().hex,
            node_type=kind,
            x=x,
            y=y,
            width=430,
            locked=False,
            data={
                "title": f"{origin.data.get('title', '素材')} · {LABELS[payload.operation.kind]}",
                "processing_origin": origin.node_key,
            },
        )
        session.add(node)
        await session.flush()
    job = Job(
        owner_id=project.owner_id,
        project_id=project.id,
        job_type=JOB_TYPE,
        target_type="canvas_node",
        target_id=node.id,
        provider="本地媒体处理",
        model="FFmpeg",
        cost_estimate=0,
        max_attempts=1,
        payload={
            "processing": payload.model_dump(),
            "origin_node_id": origin.id,
            "metadata": metadata,
            "output_kind": kind,
            "source_hash": await asyncio.to_thread(digest_file, path),
            "audio_hash": audio_hash,
            "director_context": director_context,
            "parameters": {
                "source_node_key": node.node_key,
                "canvas_request_id": payload.request_id,
                "canvas_request_digest": digest,
            },
        },
    )
    session.add(job)
    await session.flush()
    node.data = {**node.data, "job_id": job.id, "generation_status": "queued"}
    if node.id != origin.id:
        session.add(
            CanvasEdge(
                canvas_id=node.canvas_id,
                edge_key=uuid4().hex,
                source_key=origin.node_key,
                target_key=node.node_key,
                data={"processing_job_id": job.id},
            )
        )
    await session.flush()
    return job


async def preflight(session, job, retry=False):
    if job.payload.get("asset_split"):
        from app.services import asset_split_service

        return await asset_split_service.preflight(session, job, retry=retry)
    if job.payload.get("director_preview"):
        from app.services import canvas_director_preview_service

        return await canvas_director_preview_service.preflight(session, job, retry=retry)
    if job.payload.get("processing", {}).get("operation", {}).get("kind") == "split_views":
        from app.services import canvas_views_service

        return await canvas_views_service.preflight(session, job, retry=retry)
    project = await session.get(Project, job.project_id)
    node = await session.get(CanvasNode, job.target_id)
    origin = await session.get(CanvasNode, job.payload["origin_node_id"])
    if not project or not node or not origin or project.owner_id != job.owner_id:
        raise NotFoundError("处理目标或源节点已删除")
    await canvas_service.validate_job_node(session, job, node)
    await canvas_service.assert_node_unlocked(session, project, node)
    payload = ProcessMedia.model_validate(job.payload["processing"])
    _, media, path = await context(session, project, origin.node_key, payload.source_media_id)
    if origin.id != job.payload["origin_node_id"] or node.node_type != job.payload["output_kind"]:
        raise ConflictError("处理节点身份或类型已改变")
    if retry:
        await ensure_idle(session, project, node, job.id)
    if await asyncio.to_thread(digest_file, path) != job.payload["source_hash"]:
        raise ConflictError("源文件内容已改变，拒绝沿用旧处理请求")
    if payload.operation.kind == "mix_audio":
        _, audio_path, audio_metadata = await audio_context(session, project, origin, payload.operation.audio_media_id)
        if await asyncio.to_thread(digest_file, audio_path) != job.payload.get("audio_hash"):
            raise ConflictError("合成音轨内容已改变，拒绝沿用旧处理请求")
        if payload.operation.trim_end > audio_metadata["duration"] + 0.01:
            raise ConflictError("音轨裁切结束时间超出音频时长")
    return node, media, path, payload


def digest_file(path):
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def execute(job_id, worker_id):
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job and job.payload.get("asset_split"):
            from app.services import asset_split_service

            return await asset_split_service.execute(job_id, worker_id)
        if job and job.payload.get("director_preview"):
            from app.services import canvas_director_preview_service

            return await canvas_director_preview_service.execute(job_id, worker_id)
        if job and job.payload.get("processing", {}).get("operation", {}).get("kind") == "split_views":
            from app.services import canvas_views_service

            return await canvas_views_service.execute(job_id, worker_id)
    final_path, committed = None, False

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
            _, media, source, payload = await preflight(session, job)
            input_kind, kind = media.kind, job.payload["output_kind"]
            await session.commit()
        metadata = await probe(source, active)
        validate(input_kind, payload.operation, metadata)
        suffix, mime = {
            "image": (".png", "image/png"),
            "video": (".mp4", "video/mp4"),
            "audio": (".wav", "audio/wav"),
        }[kind]
        require_storage_capacity(settings)
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".processing-", dir=settings.storage_path
        ) as directory:
            output = Path(directory) / ("result" + suffix)
            audio_source = None
            if payload.operation.kind == "mix_audio":
                async with SessionLocal() as session:
                    job = await session.get(Job, job_id)
                    project = await session.get(Project, job.project_id)
                    origin = await session.get(CanvasNode, job.payload["origin_node_id"])
                    _, audio_source, _ = await audio_context(session, project, origin, payload.operation.audio_media_id)
            await command(
                build_command(source, output, kind, payload.operation, audio_source, metadata["duration"]),
                timeout=settings.media_process_timeout_seconds,
                active=active,
                output=output,
            )
            if not output.is_file() or not 0 < output.stat().st_size < MAX_BYTES:
                raise ConflictError("处理结果为空或达到文件大小上限")
            result_meta = await probe(output, active)
            if kind in {"image", "video"}:
                expected = (metadata["width"], metadata["height"])
                if payload.operation.kind == "crop":
                    expected = (payload.operation.width, payload.operation.height)
                elif payload.operation.kind == "rotate" and payload.operation.degrees in {90, 270}:
                    expected = expected[::-1]
                if (result_meta["width"], result_meta["height"]) != expected:
                    raise ConflictError("处理产物尺寸不符合要求，原素材未修改")
            if kind in {"video", "audio"} and result_meta["duration"] <= 0:
                raise ConflictError("处理结果时长无效")
            if (
                payload.operation.kind == "trim"
                and abs(result_meta["duration"] - (payload.operation.end - payload.operation.start))
                > 0.15
            ):
                raise ConflictError("处理产物时长不符合截取要求")
            if payload.operation.kind == "mix_audio" and abs(result_meta["duration"] - metadata["duration"]) > 0.15:
                raise ConflictError("音画合成产物时长与原视频不一致")
            result_hash = await asyncio.to_thread(digest_file, output)
            async with SessionLocal() as session:
                if not await job_service.renew_lease(session, job_id, worker_id):
                    return
                job = await session.get(Job, job_id)
                node = await session.get(CanvasNode, job.target_id)
                if not node or node.node_type != kind:
                    raise NotFoundError("处理目标已删除或类型改变")
                await canvas_service.validate_job_node(session, job, node)
                # Re-check source and inherited locks after the slow FFmpeg work.
                await preflight(session, job)
                relative = (
                    Path("projects") / str(job.project_id) / "processed" / (uuid4().hex + suffix)
                )
                final_path = settings.storage_path / relative
                require_storage_capacity(settings, output.stat().st_size, target=final_path)
                # Inherit permanent storage permissions, not the private temp directory ACL.
                await finish_storage_io(copy_storage_file, settings, output, final_path)
                artifact = MediaFile(
                    owner_id=job.owner_id,
                    project_id=job.project_id,
                    kind=kind,
                    source="processing",
                    file_path=relative.as_posix(),
                    original_name=f"{LABELS[payload.operation.kind]}-{payload.source_media_id}{suffix}",
                    mime_type=mime,
                    size=final_path.stat().st_size,
                    hash=result_hash,
                    width=result_meta["width"] or None,
                    height=result_meta["height"] or None,
                    duration=result_meta["duration"] if kind != "image" else None,
                )
                session.info["media_quota_consuming_job"] = job_id
                session.add(artifact)
                await session.flush()
                session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=artifact.id))
                await canvas_service.record_job_media_version(session, job, node, artifact.id)
                provenance = {
                    "source_media_id": payload.source_media_id,
                    "operation": payload.operation.model_dump(),
                }
                node.data = {
                    **node.data,
                    "generation_status": "succeeded",
                    "media_versions": [
                        {**v, **provenance} if v.get("job_id") == job.id else v
                        for v in node.data["media_versions"]
                    ],
                }
                result = {
                    "media_file_id": artifact.id,
                    "canvas_node_id": node.node_key,
                    "kind": kind,
                    **provenance,
                    **result_meta,
                }
                if not await job_service.mark_succeeded(session, job.id, worker_id, result):
                    raise ConflictError("处理已取消或租约失效，结果未采用")
                await session.commit()
                committed = True
    finally:
        if final_path and not committed:
            final_path.unlink(missing_ok=True)
