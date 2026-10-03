"""Local export job execution for the worker."""

import asyncio
import contextlib

from app.core.config import settings
from app.core.database import SessionLocal
from app.jobs.worker_runtime_helpers import heartbeat
from app.models import Job
from app.services import (
    engineering_package_service,
    jianying_draft_service,
    job_service,
    media_service,
    premiere_xml_service,
)
from app.services.episode_edit_export_guard import assert_legacy_export_allowed


async def _execute_export_job(job_id: int, worker_id: str) -> None:
    async with SessionLocal() as session:
        candidate = await session.get(Job, job_id)
        snapshot_export = candidate and candidate.target_type == "edit_project_export"
    if snapshot_export:
        from app.services.edit_project_export_service import execute

        pulse = asyncio.create_task(heartbeat(job_id, worker_id))
        try:
            await execute(job_id, worker_id)
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse
        return
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or not await job_service.mark_processing(
            session, job_id, worker_id
        ):
            await session.rollback()
            return
        payload = dict(job.payload or {})
        owner_id = job.owner_id
        await session.commit()

    pulse = asyncio.create_task(heartbeat(job_id, worker_id))
    try:
        async with SessionLocal() as session:
            session.info["media_quota_consuming_job"] = job_id
            if payload.get("episode_id") and job.target_type == engineering_package_service.TARGET_TYPE:
                media = await engineering_package_service.build_package(session, job)
                completed = await job_service.mark_succeeded(
                    session,
                    job_id,
                    worker_id,
                    {
                        "media_file_id": media.id,
                        "media_url": f"/api/media/{media.id}",
                        "episode_id": int(payload["episode_id"]),
                        "package_fingerprint": payload.get("package_fingerprint"),
                    },
                )
                if not completed:
                    await session.rollback()
                    path = (settings.storage_path / media.file_path).resolve()
                    if path.is_relative_to(settings.storage_path.resolve()):
                        path.unlink(missing_ok=True)
                    return
                await session.commit()
                return
            if payload.get("episode_id") and job.target_type == premiere_xml_service.TARGET_TYPE:
                media = await premiere_xml_service.build_package(session, job)
                completed = await job_service.mark_succeeded(
                    session,
                    job_id,
                    worker_id,
                    {
                        "media_file_id": media.id,
                        "media_url": f"/api/media/{media.id}",
                        "episode_id": int(payload["episode_id"]),
                        "package_fingerprint": payload.get("package_fingerprint"),
                        "application_validation": "not_run",
                    },
                )
                if not completed:
                    await session.rollback()
                    path = (settings.storage_path / media.file_path).resolve()
                    if path.is_relative_to(settings.storage_path.resolve()):
                        path.unlink(missing_ok=True)
                    return
                await session.commit()
                return
            if payload.get("episode_id") and job.target_type == jianying_draft_service.TARGET_TYPE:
                media = await jianying_draft_service.build_package(session, job)
                completed = await job_service.mark_succeeded(
                    session,
                    job_id,
                    worker_id,
                    {
                        "media_file_id": media.id,
                        "media_url": f"/api/media/{media.id}",
                        "episode_id": int(payload["episode_id"]),
                        "package_fingerprint": payload.get("package_fingerprint"),
                        "application_validation": "not_run",
                        "target_version": jianying_draft_service.TARGET_VERSION,
                    },
                )
                if not completed:
                    await session.rollback()
                    path = (settings.storage_path / media.file_path).resolve()
                    if path.is_relative_to(settings.storage_path.resolve()):
                        path.unlink(missing_ok=True)
                    return
                await session.commit()
                return
            episode_id = int(payload["episode_id"])
            await assert_legacy_export_allowed(session, episode_id)
            media = await media_service.export_episode_video(
                session,
                owner_id,
                episode_id,
                background_audio_media_id=payload.get("background_audio_media_id"),
                background_audio_volume=float(
                    payload.get("background_audio_volume", 0.3)
                ),
                include_subtitles=bool(payload.get("include_subtitles", True)),
                plan_id=payload.get("plan_id"),
                segment_video_version_ids=payload.get("segment_video_version_ids"),
                production_snapshot=payload.get("production_snapshot"),
            )
            try:
                async with SessionLocal() as guard_session:
                    await assert_legacy_export_allowed(guard_session, episode_id)
            except Exception:
                await session.rollback()
                path = (settings.storage_path / media.file_path).resolve()
                if path.is_relative_to(settings.storage_path.resolve()):
                    path.unlink(missing_ok=True)
                raise
            completed = await job_service.mark_succeeded(
                session,
                job_id,
                worker_id,
                {
                    "media_file_id": media.id,
                    "media_url": f"/api/media/{media.id}",
                    "episode_id": int(payload["episode_id"]),
                },
            )
            if not completed:
                await session.rollback()
                path = (settings.storage_path / media.file_path).resolve()
                if path.is_relative_to(settings.storage_path.resolve()):
                    path.unlink(missing_ok=True)
                return
            await session.commit()
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse
