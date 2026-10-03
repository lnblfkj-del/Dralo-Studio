"""Recoverable ToAPIs image execution. Re-entry only polls, never re-submits."""

# ruff: noqa: RUF001
from uuid import uuid4

from app.core.database import SessionLocal
from app.core.errors import GenerationFailedError, RemoteJobDeferredError
from app.models import Job, utcnow
from app.providers import toapis_image
from app.services import job_service


async def execute(job_id, worker_id, adapter, model, prompt, refs, params):
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job or not await job_service.renew_lease(session, job_id, worker_id):
            return None
        saved = job.payload.get("image_submission", {})
        if saved:
            if saved.get("base_url") != adapter.base_url or saved.get("model") != model:
                raise GenerationFailedError("原图片任务渠道或模型已改变，请恢复配置后查询")
        else:
            # Commit BEFORE any upload or paid request. If POST loses its response,
            # the provider's documented client_business_id GET is the only recovery.
            saved = {
                "started": True,
                "business_id": "drama-" + uuid4().hex,
                "base_url": adapter.base_url,
                "model": model,
            }
            job.payload = {**job.payload, "image_submission": saved}
        await session.commit()
        new_submission = not job.payload.get("image_submission", {}).get("attempted")
        # A distinct committed attempt marker guards even process death before POST.
        if new_submission:
            saved = {**saved, "attempted": True}
            job.payload = {**job.payload, "image_submission": saved}
            await session.commit()
    if new_submission:
        task_id = await toapis_image.submit(
            adapter,
            model=model,
            prompt=prompt,
            reference_images=refs,
            values=params,
            business_id=saved["business_id"],
        )
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job:
                saved = {**saved, "id": task_id}
                job.payload = {**job.payload, "image_submission": saved}
                await session.commit()  # Keep ID even if user cancelled during POST.
    task_id = saved.get("id") or saved["business_id"]
    # Cancellation can win while the paid submit request is in flight. Keep the
    # returned task ID for audit/recovery, but never continue polling/download.
    async with SessionLocal() as session:
        if not await job_service.renew_lease(session, job_id, worker_id):
            return None
        await session.commit()
    status = await toapis_image.poll(adapter, task_id)
    if status["status"] == "completed":
        async with SessionLocal() as session:
            active = await job_service.mark_downloading(session, job_id, worker_id)
            await session.commit()
        return await toapis_image.download(adapter, status["image_url"]) if active else None
    if status["status"] in {"failed", "cancelled", "canceled"}:
        raise GenerationFailedError("渠道图片任务已失败或取消；保留原任务编号，不自动再次计费")
    progress = status.get("progress", 0)
    retry_after = status.get("retry_after", 5)
    delay = float(retry_after) if isinstance(retry_after, int | float) else 5.0
    async with SessionLocal() as session:
        current = await session.get(Job, job_id)
        if status.get("rate_limited") is True and current is not None:
            cooldown = await job_service.record_model_rate_limit(
                session,
                current,
                retry_after_seconds=max(1, int(delay)),
            )
            if cooldown is not None:
                delay = max(delay, (cooldown - utcnow()).total_seconds())
        deferred = await job_service.defer_remote_job(
            session,
            job_id,
            worker_id,
            progress=progress if type(progress) is int else 20,
            delay_seconds=delay,
        )
        await session.commit()
    if deferred:
        raise RemoteJobDeferredError
    return None
