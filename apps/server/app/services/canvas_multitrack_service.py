"""Canvas entries reference project documents without owning their lifecycle."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.edit_project import EditProject


async def validate_inputs(session, project, payload):
    from app.models import MediaFile, ProjectMediaLink
    from app.services.team_access import owner_scope

    nodes = {node.id: node for node in payload.nodes}
    for edge in payload.edges:
        if nodes[edge.target].type != "multitrack" or edge.data.get("purpose") != "edit_input":
            continue
        source = nodes[edge.source]
        media_id = source.data.get("media_id")
        if media_id is None:
            continue
        if type(media_id) is not int or media_id <= 0:
            raise ConflictError("Invalid edit input media")
        media = await session.scalar(
            select(MediaFile).where(
                MediaFile.id == media_id, owner_scope(MediaFile.owner_id, project.owner_id)
            )
        )
        linked = await session.scalar(
            select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == project.id,
                ProjectMediaLink.media_file_id == media_id,
            )
        )
        expected = "audio" if source.type in {"audio", "voice"} else "video"
        if not media or not linked or media.kind != expected:
            raise ConflictError("Edit input is unavailable in this project")


async def validate_binding(session, project, data):
    # Outputs are derived from successful exports, never client-supplied.
    data.pop("media_id", None)
    data.pop("media_versions", None)
    data.pop("edit_summary", None)
    identity = data.get("edit_project_id")
    if identity is None:
        return
    if type(identity) is not int or identity <= 0:
        raise ConflictError("Invalid edit document reference")
    row = await session.scalar(
        select(EditProject).where(EditProject.id == identity, EditProject.project_id == project.id)
    )
    if row is None:
        raise ConflictError("Edit document is unavailable in this project")


async def enrich(session, project, nodes):
    from app.core.errors import AppError
    from app.models import Job, MediaFile
    from app.services.canvas_processing_service import file_path

    entries = [node for node in nodes if node.node_type == "multitrack"]
    identities = {
        node.data.get("edit_project_id")
        for node in entries
        if type(node.data.get("edit_project_id")) is int
    }
    if not identities:
        return {}
    rows = {
        row.id: row
        for row in (
            await session.scalars(
                select(EditProject).where(
                    EditProject.id.in_(identities), EditProject.project_id == project.id
                )
            )
        ).all()
    }
    jobs = (
        await session.scalars(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.target_type == "edit_project_export",
                Job.target_id.in_(rows),
                Job.status == "succeeded",
                Job.deleted_at.is_(None),
            )
            .order_by(Job.id.desc())
        )
    ).all()
    output = {}
    for job in jobs:
        if job.target_id in output or job.payload.get("request", {}).get("format", "mp4") != "mp4":
            continue
        media_id = (job.result or {}).get("media_file_id")
        media = await session.get(MediaFile, media_id) if type(media_id) is int else None
        if not media or media.project_id != project.id or media.kind != "video":
            continue
        try:
            file_path(media)
        except AppError:
            continue
        output[job.target_id] = media.id
    result = {}
    for node in entries:
        row = rows.get(node.data.get("edit_project_id"))
        if not row:
            result[node.node_key] = {"media_id": None, "edit_summary": None}
            continue
        document = row.document or {}
        clips = document.get("clips", [])
        result[node.node_key] = {
            "media_id": output.get(row.id),
            "edit_summary": {
                "title": row.title,
                "revision": row.revision,
                "clip_count": len(clips),
                "frame_rate": row.frame_rate,
            },
        }
    return result
