"""Reclaim only unreferenced local parser files, never project sources or assets."""

import logging
import re
import time

from sqlalchemy import select

from app.models import Job
from app.services.reference_parse_service import JOB_TYPE, directory, path
from app.services.source_index_service import parse_source_async

_NAME = re.compile(r"[a-f0-9]{32}(?:\.json)?")
logger = logging.getLogger(__name__)


def references(payload, result):
    return {name for name in ((payload or {}).get("source_file"), (result or {}).get("result_file")) if isinstance(name, str) and _NAME.fullmatch(name)}


async def cleanup(session, names=None):
    # Recycled, failed and cancelled jobs still own their files for recovery.
    keep = set()
    for payload, result in (await session.execute(select(Job.payload, Job.result).where(Job.job_type == JOB_TYPE))).all():
        keep.update(references(payload, result))
    explicit = set(names) if names is not None else None

    def remove():
        removed = 0
        cutoff = time.time() - 24 * 3600
        for candidate in directory().iterdir():
            if not _NAME.fullmatch(candidate.name) or candidate.name in keep:
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if explicit is not None and candidate.name not in explicit:
                continue
            # Allow in-flight file writes and DB commits to finish before orphan collection.
            try:
                if explicit is None and candidate.stat().st_mtime > cutoff:
                    continue
                path(candidate.name).unlink(missing_ok=True)
                removed += 1
            except OSError:
                logger.warning("Parser file cleanup deferred: %s", candidate.name)
        return removed

    return await parse_source_async(remove)


async def after_commit(session):
    names = session.info.pop("reference_parse_cleanup", set())
    if names:
        try:
            await cleanup(session, names)
        except Exception:
            # Deletion is committed. The periodic orphan sweep can retry cleanup.
            logger.exception("Parser cleanup deferred after task purge")
