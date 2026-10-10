"""Saved-document export preflight; never falls back to a production plan."""

# ruff: noqa: RUF001 -- Chinese user-facing punctuation is intentional.

import asyncio
import json
from hashlib import file_digest, sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import AppError, ConflictError
from app.models import EditProject, Job, MediaFile, ProjectMediaLink
from app.schemas.edit_project_export import EditExportPreflight, EditExportPreflightRequest
from app.services import edit_project_source_service, job_service, worker_runtime_service
from app.services.canvas_processing_service import executables, file_path
from app.services.edit_project_archive import build_archive
from app.services.edit_project_caption_browser import (
    output_dimensions,
    runtime_status,
    validate_captions,
)
from app.services.edit_project_export_capabilities import package_blockers
from app.services.edit_project_service import get_edit_project
from app.services.edit_project_snapshot_render import render_snapshot
from app.services.edit_project_source_service import verify_project_sources
from app.services.episode_edit_contract import (
    document_fingerprint,
    parse_document,
    validate_document,
)

TARGET = "edit_project_export"


async def source_dimensions(session, document, preset):
    if preset in {"landscape", "portrait"}:
        return output_dimensions(preset)
    clips = [clip for clip in document.clips if clip.track == "video"] if document else []
    if not clips:
        return 1920, 1080
    clip = min(clips, key=lambda item: item.timeline_start_frame)
    media = await session.get(MediaFile, clip.media_file_id)
    metadata = await edit_project_source_service.probe_media_file(file_path(media), "video")
    width, height = int(metadata.get("width") or 0), int(metadata.get("height") or 0)
    if min(width, height) < 2 or max(width, height) > 4096:
        raise ConflictError("视频输出尺寸不可用或超过 4096 像素限制")
    return width // 2 * 2, height // 2 * 2


def fingerprint(document_fingerprint, evidence, preset, format="mp4"):
    identity = {"document": document_fingerprint, "evidence": evidence, "preset": preset}
    if format != "mp4":
        identity["format"] = format
    return sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def worker_blockers(session, format):
    issues = []
    workers = await worker_runtime_service.status(session, include_workers=True)
    required = {TARGET, "edit_project_video_viewport"}
    if format == "archive":
        required.add("edit_project_archive")
    if any(
        "export" in item["capabilities"]
        and (
            not required.issubset(item["capabilities"])
            or item["version"] != worker_runtime_service.RUNTIME_VERSION
        )
        for item in workers.get("workers", [])
    ):
        issues.append("仍有旧版导出 Worker 在线，请统一更新后再提交")
    if not any(
        required.issubset(item["capabilities"])
        and item["version"] == worker_runtime_service.RUNTIME_VERSION
        for item in workers.get("workers", [])
    ):
        issues.append("剪辑导出 Worker 未在线，尚未提交任务")
    return issues


async def preflight(session, *, project_id, edit_project_id, owner_id, payload):
    payload = EditExportPreflightRequest.model_validate(payload)
    project = await get_edit_project(
        session, project_id=project_id, edit_project_id=edit_project_id, owner_id=owner_id
    )
    if (project.revision, project.fingerprint) != (
        payload.expected_revision,
        payload.expected_fingerprint,
    ):
        raise ConflictError("剪辑文档已更新，请重新载入后预检")
    row = await session.get(EditProject, edit_project_id)
    document = project.document
    blockers = package_blockers(document, payload.format)
    if document is not None and getattr(row, "source_episode_id", None):
        from app.services.audio_policy import for_episode
        policy = await for_episode(session, project_id, row.source_episode_id)
        if policy["background_music"] is False and any(clip.track == "bgm" for clip in document.clips):
            blockers.append("本集关闭了背景音乐，但时间线存在配乐。音乐已保留，请核对配乐设置或时间线后再导出。")
    if payload.preset == "stage":
        blockers.append("旧舞台画幅导出已停用，请选择视频原始画幅后重新预检")
    if document is None or not any(clip.track == "video" for clip in document.clips):
        blockers.append("时间线没有视频，请先加入视频并保存")
    else:
        frames = await verify_project_sources(
            session,
            project_id=project_id,
            owner_id=owner_id,
            document=document,
            expected_evidence=row.source_evidence,
        )
        validate_document(document, frames)
        if project.duration_frames / project.frame_rate > 600:
            blockers.append("当前单次导出最多支持十分钟，请先裁切")
        try:
            validate_captions(document)
        except ConflictError as exc:
            blockers.append(exc.message)
    runtime = runtime_status()
    if not runtime["ready"]:
        blockers.append(runtime["message"])
    try:
        executables()
    except ConflictError as exc:
        blockers.append(exc.message)
    blockers.extend(await worker_blockers(session, payload.format))
    width, height = await source_dimensions(session, document, payload.preset)
    return EditExportPreflight(
        format=payload.format,
        edit_project_id=project.id,
        revision=project.revision,
        fingerprint=project.fingerprint,
        frame_rate=project.frame_rate,
        duration_frames=project.duration_frames,
        clip_count=project.clip_count,
        blockers=blockers,
        status="blocked" if blockers else "ready",
        preset=payload.preset,
        width=width,
        height=height,
        preflight_fingerprint=fingerprint(
            project.fingerprint, row.source_evidence or {}, payload.preset, payload.format
        ),
    )


async def create(session, *, project_id, edit_project_id, owner_id, payload):
    await get_edit_project(
        session, project_id=project_id, edit_project_id=edit_project_id, owner_id=owner_id
    )
    query = select(Job).where(
        Job.project_id == project_id, Job.target_type == TARGET, Job.target_id == edit_project_id
    )
    public = payload.model_dump(mode="json")

    async def replay():
        previous = await session.scalar(
            query.where(
                Job.owner_id == owner_id,
                Job.payload["request_id"].as_string() == payload.request_id,
            )
        )
        if previous and previous.payload["request"] != public:
            raise ConflictError("导出请求标识已用于其他参数")
        return previous

    previous = await replay()
    if previous:
        return previous
    locked = await session.execute(
        update(EditProject)
        .where(
            EditProject.id == edit_project_id,
            EditProject.project_id == project_id,
            EditProject.revision == payload.expected_revision,
            EditProject.fingerprint == payload.expected_fingerprint,
        )
        .values(revision=payload.expected_revision)
    )
    if locked.rowcount != 1:
        raise ConflictError("剪辑文档已变化，请重新预检")
    previous = await replay()
    if previous:
        return previous
    if await session.scalar(
        query.where(Job.deleted_at.is_(None), Job.status.not_in(job_service.TERMINAL_STATUSES))
    ):
        raise ConflictError("当前剪辑已有导出任务")
    checked = await preflight(
        session,
        project_id=project_id,
        edit_project_id=edit_project_id,
        owner_id=owner_id,
        payload=EditExportPreflightRequest.model_validate(
            {
                k: public[k]
                for k in ("expected_revision", "expected_fingerprint", "format", "preset")
            }
        ),
    )
    if checked.status != "ready":
        raise ConflictError("；".join(checked.blockers))
    if checked.preflight_fingerprint != payload.preflight_fingerprint:
        raise ConflictError("导出预检已过期，请重新预检")
    row = await session.get(EditProject, edit_project_id)
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type="export",
        target_type=TARGET,
        target_id=edit_project_id,
        provider="local",
        model="ffmpeg-edit-snapshot",
        cost_estimate=0,
        max_attempts=1,
        payload={
            "request_id": payload.request_id,
            "request": public,
            "document": row.document,
            "source_evidence": row.source_evidence,
            "source_episode_id": row.source_episode_id,
            "title": row.title,
            "snapshot_fingerprint": checked.preflight_fingerprint,
            "parameters": {"scope_key": checked.preflight_fingerprint},
        },
    )
    session.add(job)
    await session.flush()
    return job


async def verify_job(session, job):
    await get_edit_project(
        session, project_id=job.project_id, edit_project_id=job.target_id, owner_id=job.owner_id
    )
    document = parse_document(job.payload["document"])
    request = job.payload["request"]
    if request["preset"] == "stage":
        raise ConflictError("旧舞台导出任务已停用，请按视频画幅重新提交")
    digest = document_fingerprint(document)
    if (
        digest != request["expected_fingerprint"]
        or document.revision != request["expected_revision"]
        or fingerprint(
            digest, job.payload["source_evidence"], request["preset"], request.get("format", "mp4")
        )
        != job.payload["snapshot_fingerprint"]
    ):
        raise ConflictError("导出快照内容不一致，请停止导出并核对")
    issues = package_blockers(document, request.get("format", "mp4"))
    if issues:
        raise ConflictError("；".join(issues))
    if not runtime_status()["ready"]:
        raise ConflictError(runtime_status()["message"])
    validate_captions(document)
    frames = await verify_project_sources(
        session,
        project_id=job.project_id,
        owner_id=job.owner_id,
        document=document,
        expected_evidence=job.payload["source_evidence"],
    )
    validate_document(document, frames)
    return document


async def execute(job_id, worker_id):
    committed, output, preview = False, None, None

    async def active():
        async with SessionLocal() as session:
            value = await job_service.renew_lease(session, job_id, worker_id)
            await session.commit()
            return value

    async def progress(value):
        async with SessionLocal() as session:
            await session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.worker_id == worker_id,
                    Job.status == "processing",
                    Job.progress < value,
                )
                .values(progress=min(95, value))
            )
            await session.commit()

    try:
        async with SessionLocal() as session:
            if not await job_service.mark_processing(session, job_id, worker_id):
                await session.rollback()
                return
            job = await session.get(Job, job_id)
            document = await verify_job(session, job)
            paths = {
                c.media_file_id: file_path(await session.get(MediaFile, c.media_file_id))
                for c in document.clips
                if c.media_file_id is not None
            }
            evidence = job.payload["source_evidence"]
            format = job.payload["request"].get("format", "mp4")
            size = await source_dimensions(session, document, job.payload["request"]["preset"])
            relative = (
                Path("projects")
                / str(job.project_id)
                / "edit-exports"
                / f"{uuid4().hex}{'.zip' if format == 'archive' else '.mp4'}"
            )
            output = settings.storage_path / relative
            preview = output.with_suffix(".preview.mp4") if format == "archive" else None
            snapshot = job.payload["snapshot_fingerprint"]
            await session.commit()
        result = await render_snapshot(
            document,
            evidence=evidence,
            paths_by_media=paths,
            output=preview or output,
            available_fonts=[],
            browser_captions=True,
            output_size=size,
            active=active,
            progress=progress,
            content_only=True,
        )
        if format == "archive":
            await progress(85)
            await build_archive(
                document,
                evidence=evidence,
                paths=paths,
                preview=preview,
                output=output,
                snapshot=snapshot,
                active=active,
            )
        with output.open("rb") as stream:
            hashing = asyncio.create_task(asyncio.to_thread(file_digest, stream, "sha256"))
            try:
                digest = await asyncio.shield(hashing)
            except asyncio.CancelledError:
                await hashing
                raise
        async with SessionLocal() as session:
            if not await job_service.renew_lease(session, job_id, worker_id):
                await session.rollback()
                return
            job = await session.get(Job, job_id)
            await verify_job(session, job)
            session.info["media_quota_consuming_job"] = job_id
            media = MediaFile(
                owner_id=job.owner_id,
                project_id=job.project_id,
                kind="file" if format == "archive" else "video",
                source="export",
                file_path=relative.as_posix(),
                original_name=f"{job.payload['title']}-剪辑归档.zip"
                if format == "archive"
                else f"{job.payload['title']}-剪辑成片.mp4",
                mime_type="application/zip" if format == "archive" else "video/mp4",
                size=output.stat().st_size,
                hash=digest.hexdigest(),
                width=None if format == "archive" else result["width"],
                height=None if format == "archive" else result["height"],
                duration=None
                if format == "archive"
                else result["duration_frames"] / result["frame_rate"],
            )
            session.add(media)
            await session.flush()
            session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
            if not await job_service.mark_succeeded(
                session,
                job_id,
                worker_id,
                {
                    **result,
                    "media_file_id": media.id,
                    "media_url": f"/api/media/{media.id}",
                    "source_episode_id": job.payload["source_episode_id"],
                    "source_fingerprint": job.payload["request"]["expected_fingerprint"],
                    "source_revision": document.revision,
                    "snapshot_fingerprint": job.payload["snapshot_fingerprint"],
                    "preset": job.payload["request"]["preset"],
                    "format": format,
                },
            ):
                await session.rollback()
                return
            await session.commit()
            committed = True
    finally:
        if preview:
            preview.unlink(missing_ok=True)
        if output and not committed:
            output.unlink(missing_ok=True)


async def prepare_retry(session, job):
    await get_edit_project(
        session, project_id=job.project_id, edit_project_id=job.target_id, owner_id=job.owner_id
    )
    await session.execute(
        update(EditProject)
        .where(EditProject.id == job.target_id, EditProject.project_id == job.project_id)
        .values(revision=EditProject.revision)
    )
    if await session.scalar(
        select(Job.id).where(
            Job.project_id == job.project_id,
            Job.target_type == TARGET,
            Job.target_id == job.target_id,
            Job.id != job.id,
            Job.deleted_at.is_(None),
            Job.status.not_in(job_service.TERMINAL_STATUSES),
        )
    ):
        raise ConflictError("该剪辑已有进行中的导出任务")
    await verify_job(session, job)
    issues = await worker_blockers(session, job.payload["request"].get("format", "mp4"))
    if issues:
        raise ConflictError("；".join(issues))


async def history(session, *, project_id, edit_project_id, owner_id, offset=0, limit=50):
    await get_edit_project(
        session, project_id=project_id, edit_project_id=edit_project_id, owner_id=owner_id
    )
    jobs = (
        await session.scalars(
            select(Job)
            .where(
                Job.project_id == project_id,
                Job.target_id == edit_project_id,
                Job.target_type == TARGET,
                Job.deleted_at.is_(None),
            )
            .order_by(Job.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for job in jobs:
        media_id = (job.result or {}).get("media_file_id")
        media = await session.get(MediaFile, media_id) if media_id else None
        available = False
        if media and media.project_id == project_id:
            try:
                file_path(media)
                available = True
            except AppError:
                pass
        items.append(
            {
                "id": job.id,
                "status": job.status,
                "progress": job.progress,
                "error_message": job.error_message,
                "revision": job.payload["request"]["expected_revision"],
                "fingerprint": job.payload["request"]["expected_fingerprint"],
                "preset": job.payload["request"]["preset"],
                "format": job.payload["request"].get("format", "mp4"),
                "created_at": job.created_at,
                "media_file_id": media_id if available else None,
                "available": available,
            }
        )
    return items
