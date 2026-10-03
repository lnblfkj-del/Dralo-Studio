"""Durable local reference parsing, isolated from model execution and billing."""

import asyncio
import contextlib
import json
import shutil
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError, ValidationError
from app.core.storage_safety import require_storage_capacity, write_storage_bytes
from app.models import Job
from app.services import job_service
from app.services.reference_service import MAX_FILE_BYTES, read_reference
from app.services.source_index_service import parse_source_async

JOB_TYPE = "source_parse"
MIN_FREE_BYTES = 256 * 1024 * 1024


def require_space():
    if shutil.disk_usage(directory()).free < MIN_FREE_BYTES:
        raise ValidationError("存储空间不足 256 MiB，已暂停原文解析。请释放存储空间后重试，已有资料不会删除。")


def directory():
    root = settings.storage_path / "reference-parses"
    if not root.is_dir():
        require_storage_capacity(settings, target=root)
        root.mkdir(parents=True, exist_ok=True)
    return root


def path(name):
    root = directory().resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValidationError("解析文件路径无效")
    return target


async def submit(session, owner_id, filename, data):
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValidationError("文件不能为空，且不能超过 20 MiB")
    if Path(filename).suffix.lower() not in {".txt", ".md", ".docx"}:
        raise ValidationError("仅支持 TXT、Markdown 和 DOCX 文件")
    token = uuid4().hex
    require_space()
    payload = {"filename": Path(filename).name}
    if settings.runtime_execution_location == "cloud":
        media = await store_original(session, owner_id, filename, data)
        payload.update(source_media_id=media.id, source_media_path=media.file_path)
    else:
        await parse_source_async(write_storage_bytes, settings, path(token), data)
        payload["source_file"] = token
    job = Job(owner_id=owner_id, job_type=JOB_TYPE, target_type="reference_parse",
              payload=payload, max_attempts=3)
    session.add(job)
    try:
        await session.flush()
    except Exception:
        if "source_file" in payload:
            path(token).unlink(missing_ok=True)
        elif "source_media_path" in payload:
            source_path(payload).unlink(missing_ok=True)
        raise
    return job


async def store_original(session, owner_id, filename, data):
    from fastapi import UploadFile
    from app.services.media_processing_service import save_upload
    stream = UploadFile(BytesIO(data), filename=filename, size=len(data))
    mime = {".txt": "text/plain", ".md": "text/markdown",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(Path(filename).suffix.lower())
    try:
        media = await save_upload(session, owner_id=owner_id, stream=stream,
                                  filename=filename, content_type=mime, project=None)
        from app.services.source_file_transaction import track
        track(session, settings.storage_path, Path(media.file_path))
        return media
    finally:
        await stream.close()


def source_path(payload):
    if "source_media_path" not in payload:
        return path(payload["source_file"])
    root = settings.storage_path.resolve()
    target = (root / payload["source_media_path"]).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValidationError("原始文档不存在，无法继续解析")
    return target


def parse_saved(payload, result_name):
    require_space()
    result = read_reference(payload["filename"], source_path(payload).read_bytes())
    write_storage_bytes(settings, path(result_name), json.dumps(result, ensure_ascii=False).encode("utf-8"))


async def execute(job_id, worker_id):
    from app.jobs.worker_runtime_helpers import heartbeat
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job or not await job_service.mark_processing(session, job_id, worker_id):
            await session.rollback()
            return
        payload = dict(job.payload)
        if "source_media_id" in payload:
            from app.models import MediaFile
            original = await session.get(MediaFile, payload["source_media_id"])
            if original is None or original.owner_id != job.owner_id or original.file_path != payload.get("source_media_path"):
                raise ValidationError("原始文档归属或版本不一致，已停止解析")
        await session.commit()
    pulse = asyncio.create_task(heartbeat(job_id, worker_id))
    result_name = uuid4().hex + ".json"
    published = False
    try:
        await parse_source_async(parse_saved, payload, result_name)
        async with SessionLocal() as session:
            completed = await job_service.mark_succeeded(session, job_id, worker_id, {"result_file": result_name})
            await session.commit()
            published = completed
    finally:
        if not published:
            path(result_name).unlink(missing_ok=True)
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse


async def result(job):
    if job.job_type != JOB_TYPE or job.status != "succeeded":
        raise ConflictError("原文解析尚未完成，请查看任务状态后重试")
    return await parse_source_async(lambda: json.loads(path(job.result["result_file"]).read_text(encoding="utf-8")))
