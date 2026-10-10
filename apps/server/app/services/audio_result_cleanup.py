"""Bounded local receipt cleanup; never removes adopted media or remote objects."""

import asyncio
import re
import time

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError
from app.core.workspace_context import system_scope
from app.models import Job, MediaFile
from app.services.audio_result_lifecycle import result_expired
from app.services.storage_cleanup_service import local_path

_cursor = ""


async def sweep(limit=100):
    global _cursor
    root = local_path("audio-results")
    if not root.is_dir():
        return 0
    # Only managed receipt names are eligible. No recursive traversal or arbitrary paths.
    names = await asyncio.to_thread(lambda: sorted(
        p.name for p in root.iterdir() if p.name > _cursor
        and re.fullmatch(r"[1-9][0-9]*-[a-f0-9]{32}\.bin", p.name)
    )[:limit])
    _cursor = names[-1] if len(names) == limit else ""
    cutoff = time.time() - settings.audio_result_retention_days * 86400
    removed = 0
    with system_scope():
        async with SessionLocal() as session:
            for name in names:
                path = local_path("audio-results/" + name)
                if not path.is_file():
                    continue
                ident, token = name[:-4].split("-")
                job = await session.scalar(select(Job).where(Job.id == int(ident)).with_for_update(skip_locked=True))
                # A locked or missing row must not be mistaken for an orphan.
                if job is None:
                    exists = await session.scalar(select(Job.id).where(Job.id == int(ident)))
                    if exists or path.stat().st_mtime >= cutoff:
                        continue
                else:
                    saved = (job.payload or {}).get("audio_submission", {})
                    if (job.status not in {"succeeded", "failed", "cancelled"}
                            or job.job_type not in {"tts", "audio"} or job.lease_expires_at
                            ):
                        continue
                    matching = saved.get("receipt_token") == token
                    if not matching and path.stat().st_mtime >= cutoff:
                        continue
                    if matching and not result_expired(job) and (saved.get("receipt_expires_at") or path.stat().st_mtime >= cutoff):
                        continue
                relative = path.relative_to(settings.storage_path.resolve()).as_posix()
                if await session.scalar(select(MediaFile.id).where(MediaFile.file_path == relative).limit(1)):
                    continue
                if job and matching and job.project_id and job.target_type in {"asset", "canvas_node"}:
                    output = (f"projects/{job.project_id}/assets/{job.target_id}/audio-job-{job.id}.mp3"
                              if job.target_type == "asset" else f"projects/{job.project_id}/audio/audio-job-{job.id}.mp3")
                    referenced = await session.scalar(select(MediaFile.id).where(MediaFile.file_path == output).limit(1))
                    if not referenced:
                        # Retain the receipt until orphan removal succeeds, so cleanup can retry.
                        try:
                            await asyncio.to_thread(local_path(output).unlink, missing_ok=True)
                        except (OSError, ConflictError):
                            continue
                try:
                    await asyncio.to_thread(path.unlink)
                except OSError:
                    continue
                if job and matching:
                    job.payload = {**job.payload, "audio_submission": {**saved, "receipt_expired": True}}
                removed += 1
            await session.commit()
    return removed


async def supervise():
    from app.core.logging import get_logger
    while True:
        try:
            await sweep()
        except (OSError, ConflictError):
            get_logger(__name__).warning("Audio receipt cleanup deferred: unsafe or inaccessible storage")
        except Exception:
            get_logger(__name__).exception("Audio receipt cleanup deferred")
        await asyncio.sleep(300)
