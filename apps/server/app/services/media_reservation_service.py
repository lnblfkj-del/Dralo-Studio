"""Reserve before billable media submission, without holding a DB lock across HTTP."""

# ruff: noqa: RUF001

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError
from app.core.media_quota import lock_workspace, reserved_media_bytes, workspace_media_limit, pending_cleanup_bytes
from app.models import Job, MediaFile, MediaReservation

OUTPUT_LIMITS = {"image": 20 * 1024 ** 2, "video": 500 * 1024 ** 2, "tts": 100 * 1024 ** 2}


async def reserve(job_id, worker_id):
    if settings.runtime_execution_location != "cloud":
        return
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or job.job_type not in {*OUTPUT_LIMITS, "media_process", "export"}:
            return
        await session.run_sync(lock_workspace, job.workspace_id)
        await session.refresh(job)
        if job.worker_id != worker_id or job.status in {"succeeded", "failed", "cancelled"}:
            raise ConflictError("任务状态已变化，未提交模型请求")
        size = OUTPUT_LIMITS.get(job.job_type, 500 * 1024**2)
        if job.job_type == "media_process":
            payload = job.payload or {}
            split = payload.get("asset_split") or {}
            count = len(split.get("regions") or [])
            if not count:
                count = len(payload.get("target_ids") or [])
            if count:
                size = count * 20 * 1024**2
            elif payload.get("output_kind") == "image":
                size = 20 * 1024**2
        held = await session.run_sync(reserved_media_bytes, job.workspace_id)
        old = await session.get(MediaReservation, job.id)
        used = int(await session.scalar(select(func.coalesce(func.sum(MediaFile.size), 0)).where(
            MediaFile.workspace_id == job.workspace_id)) or 0)
        limit = await session.run_sync(workspace_media_limit, job.workspace_id)
        used += await session.run_sync(pending_cleanup_bytes, job.workspace_id)
        if job.job_type == "export" and job.target_type != "episode":
            # Archives can contain source media of varying size. Exclusively reserve
            # the remaining account capacity instead of inventing a package size cap.
            size = old.size if old else max(0, limit - used - held)
        if size <= 0 or used + held + (0 if old else size) > limit:
            label = {"image": "图片", "video": "视频", "tts": "音频", "media_process": "本地处理", "export": "导出"}[job.job_type]
            raise ConflictError(
                f"素材空间不足：本次{label}任务需预占 {size // 1024 ** 2} MiB，已用或其他任务预占占满额度；请清理素材或联系管理员调额，未提交新的模型请求",
                details={"reason": "media_quota_insufficient", "required_bytes": size,
                         "used_bytes": used, "reserved_bytes": held, "limit_bytes": limit})
        if old is None:
            session.add(MediaReservation(job_id=job.id, workspace_id=job.workspace_id, size=size))
        await session.commit()
