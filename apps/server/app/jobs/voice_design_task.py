"""Voice previews use the audio queue, but never the preset TTS request path."""

# ruff: noqa: RUF001
import asyncio
import contextlib
from datetime import timedelta
from uuid import uuid4

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import AppError, GenerationFailedError
from app.core.storage_safety import finish_storage_io
from app.jobs.audio_task import has_receipt
from app.jobs.worker_runtime_helpers import heartbeat
from app.models import Job, utcnow
from app.services import billing_service, job_service
from app.services import voice_design_service as design
from app.services.audio_result_lifecycle import result_expired


async def execute(job_id, worker_id):
    call_id = None
    pulse = asyncio.create_task(heartbeat(job_id, worker_id))
    try:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or not await job_service.renew_lease(session, job_id, worker_id):
                return
            if job.execution_phase not in job_service.REMOTE_EXECUTION_PHASES and not await job_service.mark_processing(session, job_id, worker_id):
                return
            await design.validate_target(session, job)
            saved = job.payload.get("audio_submission") or {}
            result = None
            if saved:
                if result_expired(job):
                    raise GenerationFailedError("音色设计回执已过期，不会重新生成")
                result = await asyncio.to_thread(design.load_receipt, job)
            if result is None:
                provider, model = await design.design_model(session, job.payload["provider_model_id"], job.payload["voice_scope"])
                if job.target_type == "model_audio_test" or job.payload.get("parameters", {}).get("audio_request_id"):
                    from app.providers.protocols import execution_contract
                    from app.services.audio_verification_service import require_ready
                    require_ready(provider, model, require_voice=False)
                    if job.payload["protocol_contract"] != execution_contract(provider, model):
                        raise GenerationFailedError("音色测试配置已变化，请重新确认")
                if saved.get("started"):
                    raise GenerationFailedError("音色设计提交结果待核对，禁止自动重发")
                adapter = design.transport(provider, model)
                job.payload = {**job.payload, "audio_submission":{"started":True, "receipt_token":uuid4().hex,
                    "started_at":utcnow().isoformat(),
                    "receipt_expires_at":(utcnow() + timedelta(days=settings.audio_result_retention_days)).isoformat()}}
            await session.commit()
        call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:voice-design")
        if result is None:
            result = await adapter._post("/text-to-voice/design", {"voice_description":job.payload["prompt"],
                "text":job.payload["parameters"]["text"], "model_id":job.payload["parameters"]["design_model"],
                "auto_generate_text":False, "stream_previews":False}, json_response=True, query={"output_format":"mp3_44100_128"})
            # Save the response before decoding or DB persistence, including malformed provider results.
            await finish_storage_io(design.save_receipt, job, result)
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            current.payload = {**current.payload, "audio_submission":{**current.payload["audio_submission"], "result_received":True}}
            if current.status not in job_service.TERMINAL_STATUSES:
                current.execution_phase = "download"
            await session.commit()
        await billing_service.observe(call_id, {"returned":True})
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            if not current or not await job_service.renew_lease(session, job_id, worker_id):
                return
            session.info["media_quota_consuming_job"] = current.id
            persisted = await design.persist(session, current, result)
            if not await job_service.mark_succeeded(session, job_id, worker_id, persisted):
                await session.rollback()
                return
            await session.commit()
    except Exception as exc:
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            if current:
                if result_expired(current):
                    code, message = "AUDIO_RESULT_EXPIRED", "音色设计回执已过期，请重新确认费用后新建任务"
                elif isinstance(exc, AppError) and exc.details.get("audio_terminal_failure"):
                    code, message = "AUDIO_PROVIDER_FAILED", exc.message
                elif has_receipt(current):
                    code, message = "AUDIO_SAVE_FAILED", "音色设计结果已保存；重试仅恢复保存，不调用模型"
                elif current.payload.get("audio_submission", {}).get("started"):
                    code, message = "AUDIO_OUTCOME_UNKNOWN", "音色设计提交结果待核对；不会自动重发，请核对渠道记录和账单"
                else:
                    code, message = (exc.code, exc.message) if isinstance(exc, AppError) else ("GENERATION_FAILED", "音色设计准备失败")
                await job_service.mark_failed(session, job_id, worker_id, code, message)
                await session.commit()
    finally:
        try:
            await billing_service.finalize_provider_call(call_id)
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse
