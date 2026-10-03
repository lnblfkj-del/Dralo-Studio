"""Read models for media generation history."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Job, Project
from app.services.team_access import owner_scope


async def list_generation_history(
    session: AsyncSession,
    owner_id: int,
    *,
    limit: int,
    offset: int,
    status: str | None,
    project_id: int | None,
    keyword: str | None,
) -> tuple[list[dict], int]:
    conditions = [owner_scope(Job.owner_id, owner_id), Job.target_type == "asset"]
    if status:
        conditions.append(Job.status == status)
    if project_id is not None:
        conditions.append(Job.project_id == project_id)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append(or_(Job.provider.like(pattern), Job.model.like(pattern)))
    rows = (
        await session.execute(
            select(Job, Project.name, Asset.name)
            .outerjoin(Project, Project.id == Job.project_id)
            .outerjoin(Asset, Asset.id == Job.target_id)
            .where(*conditions)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    count = int(
        await session.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    )
    items = []
    for job, project_name, asset_name in rows:
        result = job.result or {}
        items.append(
            {
                "job_id": job.id,
                "project_id": job.project_id,
                "project_name": project_name,
                "asset_id": job.target_id,
                "asset_name": asset_name,
                "media_file_id": result.get("media_file_id"),
                "job_type": job.job_type,
                "status": job.status,
                "provider": job.provider,
                "model": job.model,
                "prompt": job.payload.get("prompt"),
                "parameters": job.payload.get("parameters", {}),
                "cost_estimate": job.cost_estimate,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
            }
        )
    return items, count
