"""Keep historical shots addressable without treating them as current edit targets."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Job, Shot
from app.services.job_concurrency_service import TERMINAL_STATUSES

SHOT_STATUS_SUPERSEDED = "superseded"


async def require_idle_shots(session, shot_ids: list[int]) -> None:
    if not shot_ids:
        return
    await session.execute(select(Shot.id).where(Shot.id.in_(shot_ids)).order_by(Shot.id).with_for_update())
    active = await session.scalar(select(Job.id).where(
        Job.target_type == "shot",
        Job.target_id.in_(shot_ids),
        Job.status.not_in(TERMINAL_STATUSES),
        Job.deleted_at.is_(None),
    ).limit(1))
    if active is not None:
        raise ConflictError("本集仍有镜头视频任务执行中，请完成或取消后再重拆镜头")
