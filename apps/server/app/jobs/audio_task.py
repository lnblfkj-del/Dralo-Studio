"""Durable audio results and short-lived music polls, never implicit paid resubmits."""

# ruff: noqa: RUF001
import asyncio
import contextlib
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import AppError, GenerationFailedError, RemoteJobDeferredError
from app.core.logging import get_logger
from app.core.provider_crypto import decrypt_api_key
from app.core.storage_safety import finish_storage_io, write_storage_bytes
from app.jobs.worker_runtime_helpers import heartbeat
from app.models import CanvasNode, Job, Project, Provider, ProviderModel, utcnow
from app.providers.audio_transport import MAX_AUDIO_BYTES, mp3_result
from app.providers.factory import create_provider_adapter
from app.providers.protocols import effective_protocol, execution_contract
from app.services import billing_service, canvas_agent_service, canvas_service, job_service
from app.services.audio_result_lifecycle import result_expired

logger = get_logger(__name__)


def fingerprint(job):
    payload = job.payload or {}
    return hashlib.sha256(json.dumps({key: payload.get(key) for key in (
        "provider_model_id", "prompt", "parameters", "protocol_contract")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def receipt_path(job):
    token = (job.payload or {}).get("audio_submission", {}).get("receipt_token")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        return None
    root = settings.storage_path.resolve()
    path = root / "audio-results" / f"{job.id}-{token}.bin"
    if not path.resolve().is_relative_to(root) or any(
        part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
        for part in (path, path.parent)
    ):
        raise GenerationFailedError("音频结果存储路径异常，已停止恢复")
    return path


def has_receipt(job):
    if result_expired(job):
        return False
    path = receipt_path(job)
    return path is not None and path.is_file()


def store_receipt(job, result):
    data = mp3_result(result.get("audio_bytes"))["audio_bytes"]
    path = receipt_path(job)
    if path is None:
        raise GenerationFailedError("音频结果没有持久存储标识")
    header = json.dumps({"fingerprint": fingerprint(job), "sha256": hashlib.sha256(data).hexdigest(),
        **{key: result[key] for key in ("provider_usage_chars", "provider_usage_invalid") if key in result},
        "provider_request_id": str(result.get("provider_request_id") or "")[:255]},
        separators=(",", ":")).encode()
    write_storage_bytes(settings, path, len(header).to_bytes(4, "big") + header + data)


def load_receipt(job):
    if result_expired(job):
        raise GenerationFailedError("音频恢复结果已过期；不会重新调用模型，请在原页面确认费用后新建任务",
                                    details={"audio_result_expired": True})
    path = receipt_path(job)
    if path is None or not path.is_file():
        return None
    try:
        if not 4 < path.stat().st_size <= MAX_AUDIO_BYTES + 4096:
            raise ValueError
        with path.open("rb") as stream:
            length = int.from_bytes(stream.read(4), "big")
            if not 0 < length <= 4096:
                raise ValueError
            header = json.loads(stream.read(length))
            data = stream.read(MAX_AUDIO_BYTES + 1)
        if (header["fingerprint"] != fingerprint(job)
                or header["sha256"] != hashlib.sha256(data).hexdigest()):
            raise ValueError
        metadata = {key: header[key] for key in ("provider_usage_chars", "provider_usage_invalid") if key in header}
        if "provider_usage_chars" in metadata and (type(metadata["provider_usage_chars"]) is not int or not 0 <= metadata["provider_usage_chars"] <= 1_000_000_000):
            raise ValueError
        if "provider_usage_invalid" in metadata and type(metadata["provider_usage_invalid"]) is not bool:
            raise ValueError
        return mp3_result(data, provider_request_id=header.get("provider_request_id", ""), **metadata)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GenerationFailedError("已保存音频损坏或与任务不符；不会重新调用模型，请核对存储") from exc


async def execute(job_id, worker_id):
    call_id, deferred = None, False
    pulse = asyncio.create_task(heartbeat(job_id, worker_id))
    try:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or not await job_service.renew_lease(session, job_id, worker_id):
                return
            if job.execution_phase not in job_service.REMOTE_EXECUTION_PHASES and not await job_service.mark_processing(session, job_id, worker_id):
                return
            await _validate_target(session, job)
            result = await asyncio.to_thread(load_receipt, job)
            model = await session.get(ProviderModel, job.payload["provider_model_id"])
            provider = await session.get(Provider, job.provider_id)
            saved = dict(job.payload.get("audio_submission") or {})
            if result is None:
                from app.providers.audio_contracts import music_parameters
                from app.providers.speech import speech_parameters
                from app.services.canvas_generation_service import validate_model
                model = await validate_model(session, job.payload["provider_model_id"], "audio", job.payload.get("parameters", {}))
                if provider is None or model.provider_id != provider.id:
                    raise GenerationFailedError("音频渠道配置不存在")
                if job.payload["protocol_contract"] != execution_contract(provider, model):
                    raise GenerationFailedError("音频原渠道或模型已变化，请恢复配置后继续")
                protocol = effective_protocol(provider, model)
                prompt = job.payload["prompt"]
                parameters = job.payload.get("parameters", {})
                parameters = saved["parameters"] if "parameters" in saved else (speech_parameters(model, parameters, prompt, protocol=protocol)
                    if job.job_type == "tts" else music_parameters(model, parameters, prompt, protocol))
                adapter = create_provider_adapter(provider, decrypt_api_key(provider.api_key_ciphertext), model)
                if (saved.get("started") and not saved.get("id")) or job.payload.get("media_submission", {}).get("started"):
                    raise GenerationFailedError("音频提交结果不确定，禁止自动重发；请核对渠道记录和账单")
                if not saved:
                    saved = {"started": True, "receipt_token": uuid4().hex, "started_at": utcnow().isoformat(), "parameters": parameters,
                             "receipt_expires_at": (utcnow() + timedelta(days=settings.audio_result_retention_days)).isoformat(),
                             "poll_deadline_at": (utcnow() + timedelta(minutes=30)).isoformat()}
                    job.payload = {**job.payload, "audio_submission": saved}
            await session.commit()
        call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:audio")
        if result is None:
            if protocol == "stepfun_music":
                if datetime.fromisoformat(saved["poll_deadline_at"]).replace(tzinfo=UTC) <= utcnow().replace(tzinfo=UTC):
                    raise GenerationFailedError("音乐查询已达到本轮等待上限；可手动继续查询，不会再次提交生成")
                if not saved.get("id"):
                    submitted = await adapter.submit_music(model=model.model_id, prompt=prompt, parameters=parameters)
                    saved = {**saved, "id": submitted["task_id"]}
                    async with SessionLocal() as session:
                        current = await session.get(Job, job_id)
                        # Preserve the upstream ID even if cancellation won during POST.
                        if current:
                            current.payload = {**current.payload, "audio_submission": saved}
                            current.execution_phase = "poll"
                            await session.commit()
                async with SessionLocal() as session:
                    if not await job_service.renew_lease(session, job_id, worker_id):
                        return
                    await session.commit()
                status = await adapter.query_music(saved["id"])
                if status["status"] != "SUCCESS":
                    async with SessionLocal() as session:
                        released = await job_service.defer_remote_job(session, job_id, worker_id, progress=30, delay_seconds=5)
                        await session.commit()
                    if released:
                        raise RemoteJobDeferredError
                    return
                result = status
            elif job.job_type == "audio":
                result = await adapter.generate_music(model=model.model_id, prompt=prompt, parameters=parameters)
            else:
                from app.providers.speech import generate_speech
                result = await generate_speech(adapter, model=model.model_id, prompt=prompt, parameters=parameters)
            await finish_storage_io(store_receipt, job, result)
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            if not current:
                return
            current.payload = {**current.payload, "audio_submission": {
                **current.payload.get("audio_submission", {}), "result_received": True}}
            if current.status not in job_service.TERMINAL_STATUSES:
                current.execution_phase = "download"
            await session.commit()
        await billing_service.observe(call_id, {"returned": True, "input_chars": len(job.payload["prompt"]),
            **{key: result[key] for key in ("provider_usage_chars", "provider_usage_invalid") if key in result},
            **({"external_task_id": saved["id"]} if saved.get("id") else {})})
        from app.services.generated_audio_media import inspect_audio
        result = {**result, **await inspect_audio(result["audio_bytes"])}
        await billing_service.observe(call_id, {"duration_seconds": result["duration"]})
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            if not current or not await job_service.renew_lease(session, job_id, worker_id):
                return
            await _validate_target(session, current)
            session.info["media_quota_consuming_job"] = current.id
            if current.target_type == "asset":
                from app.services import asset_audio_generation
                persisted = await asset_audio_generation.finalize(session, current, result)
            else:
                persisted = await canvas_service.finalize_audio_node_job(session, current, result)
                await canvas_agent_service.finalize_media_job(session, current, persisted, "audio")
            if not await job_service.mark_succeeded(session, job_id, worker_id, persisted):
                await session.rollback()
                return
            await session.commit()
    except RemoteJobDeferredError:
        deferred = True
    except Exception as exc:
        logger.warning("Audio task %s failed (%s)", job_id, type(exc).__name__)
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            if current:
                submission = current.payload.get("audio_submission", {})
                if result_expired(current):
                    code, message = "AUDIO_RESULT_EXPIRED", "音频恢复结果已过期；不会自动重新生成，已有素材不受影响"
                elif isinstance(exc, AppError) and exc.details.get("audio_terminal_failure"):
                    code, message = "AUDIO_PROVIDER_FAILED", exc.message
                elif has_receipt(current):
                    code, message = "AUDIO_SAVE_FAILED", "音频结果已保存，绑定或写入失败；重试保存不调用模型"
                    if isinstance(exc, AppError):
                        message += "；" + exc.message
                elif submission.get("id"):
                    code, message = "AUDIO_QUERY_FAILED", "原音乐任务查询或结果保存失败；可继续查询原任务，不重新提交生成"
                elif submission.get("started"):
                    code, message = "AUDIO_OUTCOME_UNKNOWN", "音频提交结果待核对，未自动重新生成，请核对渠道记录和账单"
                else:
                    code, message = (exc.code, exc.message) if isinstance(exc, AppError) else ("GENERATION_FAILED", "音频任务准备失败，未继续调用模型")
                await job_service.mark_failed(session, job_id, worker_id, code, message)
                await session.commit()
    finally:
        try:
            if not deferred:
                await billing_service.finalize_provider_call(call_id)
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse


async def _validate_target(session, job):
    if job.target_type == "asset":
        from app.services import asset_audio_generation
        await asset_audio_generation.target(session, job)
        return
    if job.target_type != "canvas_node":
        raise GenerationFailedError("音频任务尚未适配此输出目标")
    project = await session.get(Project, job.project_id)
    node = await session.get(CanvasNode, job.target_id)
    if project is None or node is None or (job.job_type == "audio" and node.node_type != "audio"):
        raise GenerationFailedError("音频目标已删除或类型不匹配")
    await canvas_service.validate_job_node(session, job, node)
    await canvas_service.assert_node_unlocked(session, project, node)
