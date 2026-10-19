"""Delete unreferenced model-test outputs; retain billing and request identities."""
# ruff: noqa: RUF001
import re

from sqlalchemy import select

from app.models import Asset, CanvasNode, Job, MediaFile, ProjectMediaLink
from app.models.edit_project import EditProject
from app.models.storage_cleanup import MediaCleanup
from app.services.media_delete_service import delete_media, references_media
from app.services.storage_cleanup_service import local_path
from app.core.errors import ConflictError
from app.core.config import settings


def output_path(job, name):
    prefix = f"model-tests/{job.owner_id}/"
    return name == prefix + f"audio-{job.id}.mp3" or bool(re.fullmatch(
        re.escape(prefix + f"voice-design/{job.id}-") + r"[0-9]\.mp3", name))


async def referenced(session, media):
    if await session.scalar(select(ProjectMediaLink.id).where(ProjectMediaLink.media_file_id == media.id).limit(1)):
        return True
    # Local editions also permit direct canvas/editor references without a media link.
    for query in (select(CanvasNode.data), select(EditProject.document), select(Asset.attributes),
                  select(Job.payload).where(Job.status.not_in({"succeeded", "failed", "cancelled"}))):
        for value in await session.scalars(query):
            if references_media(value, media.id):
                return True
    return False


async def queue_file(session, job, relative, size=0):
    local_path(relative)  # Reject traversal, links and junctions before queuing deletion.
    session.info.setdefault('audio_trial_cleanup_paths', set()).add(relative)
    if job.workspace_id is None:
        return
    if not await session.scalar(select(MediaCleanup.id).where(MediaCleanup.file_path == relative).limit(1)):
        session.add(MediaCleanup(workspace_id=job.workspace_id, owner_id=job.owner_id, file_path=relative, size=size))


async def after_commit(session):
    paths = session.info.pop('audio_trial_cleanup_paths', set())
    if settings.runtime_execution_location == 'cloud':
        from app.services.storage_cleanup_service import drain_media
        await drain_media(limit=max(100, len(paths)))
    else:
        from app.services.media_delete_service import delete_files
        removable = []
        for name in paths:
            if not await session.scalar(select(MediaFile.id).where(MediaFile.file_path == name).limit(1)):
                local_path(name)
                removable.append(name)
        delete_files(removable)


async def cleanup(session, job):
    rows = (await session.scalars(select(MediaFile).where(
        MediaFile.owner_id == job.owner_id, MediaFile.project_id.is_(None),
        MediaFile.purpose == "model_test",
        MediaFile.file_path.like(f"model-tests/{job.owner_id}/%"),
    ))).all()
    deleted, retained = [], []
    for media in rows:
        if not output_path(job, media.file_path):
            continue
        if await referenced(session, media):
            retained.append(media.id)
            continue
        try:
            async with session.begin_nested():
                relative = await delete_media(session, media)
                await queue_file(session, job, relative.as_posix(), media.size)
        except ConflictError:
            retained.append(media.id)
            continue
        deleted.append(media.id)
    from app.jobs.audio_task import receipt_path
    receipt = receipt_path(job)
    if receipt:
        for path in (receipt, receipt.with_suffix('.adopt.json')):
            await queue_file(session, job, path.relative_to(local_path('audio-results').parent).as_posix())
    job.payload = {**job.payload, "audio_test_cleared": True,
        "audio_submission": {**job.payload.get("audio_submission", {}), "receipt_expired": True}}
    if job.result:
        result = dict(job.result)
        if result.get('media_file_id') in deleted:
            result.pop('media_file_id', None)
        if isinstance(result.get('previews'), list):
            result['previews'] = [row for row in result['previews'] if row.get('media_file_id') not in deleted]
        job.result = {**result, 'cleared_media_ids': deleted}
    return deleted, retained
