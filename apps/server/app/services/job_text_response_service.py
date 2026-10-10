"""Persist paid text-call state before business result processing."""

import hashlib
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.core.errors import AppError, ConflictError
from app.models import JOB_STATUS_FAILED, Job, JobTextResponse, utcnow


async def mark_submission_started(
    job_id: int, worker_id: str, call_id: int, model_id: str
) -> None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or job.worker_id != worker_id:
            return
        previous = dict((job.payload or {}).get("text_submission") or {})
        job.payload = {
            **(job.payload or {}),
            "text_submission": {
                "status": "submitted",
                "call_id": call_id,
                "attempt": job.attempts,
                "model": model_id,
                "started_at": utcnow().isoformat(),
                "response_received": False,
                "previous_call_id": previous.get("call_id"),
            },
        }
        await session.commit()


async def preserve_response(
    job_id: int,
    worker_id: str,
    call_id: int,
    result: dict[str, Any],
) -> None:
    text = str(result.get("text") or "")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    finish_reason = result.get("finish_reason")
    finish_reason = finish_reason[:100] if isinstance(finish_reason, str) else None
    async with SessionLocal() as session:
        await session.execute(
            delete(JobTextResponse).where(JobTextResponse.expires_at < utcnow())
        )
        job = await session.get(Job, job_id)
        if job is None or job.worker_id != worker_id:
            return
        submission = dict((job.payload or {}).get("text_submission") or {})
        received_at = utcnow()
        from app.services.execution_policy_service import job_text_response_retention_days
        retention_days = job_text_response_retention_days(job)
        response_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        stored = await session.scalar(
            select(JobTextResponse).where(JobTextResponse.call_id == call_id)
        )
        if stored is None:
            stored = JobTextResponse(
                job_id=job.id,
                call_id=call_id,
                attempt=job.attempts,
                response_text=text,
                response_sha256=response_hash,
                response_chars=len(text),
                usage=usage,
                streaming=bool(result.get("streaming")),
                status="available",
                received_at=received_at,
                expires_at=received_at + timedelta(days=retention_days),
            )
            session.add(stored)
            await session.flush()
        job.payload = {
            **(job.payload or {}),
            "text_submission": {
                **submission,
                "status": "response_received",
                "call_id": call_id,
                "response_received": True,
                "received_at": received_at.isoformat(),
                "response_sha256": response_hash,
                "response_chars": len(text),
                "usage": usage,
                "finish_reason": finish_reason,
                **({"refusal_received": bool(result.get("refusal"))}
                   if (job.payload or {}).get("response_protocol") else {}),
                **({key: result[key][:255] for key in ("response_model", "provider_request_id")
                    if isinstance(result.get(key), str)} if (job.payload or {}).get("response_protocol") else {}),
                "stream_terminal_seen": result.get("stream_terminal_seen") if isinstance(result.get("stream_terminal_seen"), bool) else None,
                "stream_done_marker_seen": result.get("stream_done_marker_seen") if isinstance(result.get("stream_done_marker_seen"), bool) else None,
            },
            "response_recovery": {
                "schema_version": 2,
                "response_id": stored.id,
                "call_id": call_id,
                "attempt": job.attempts,
                "received_at": received_at.isoformat(),
                "expires_at": stored.expires_at.isoformat(),
                "status": "available",
            },
        }
        await session.commit()


async def mark_response_processed(
    session: AsyncSession,
    job: Job,
    call_id: int | None,
) -> None:
    if call_id is None:
        return
    stored = await session.scalar(
        select(JobTextResponse).where(JobTextResponse.call_id == call_id)
    )
    if stored is None:
        return
    processed_at = utcnow()
    stored.status = "processed"
    stored.processed_at = processed_at
    job.payload = {
        **(job.payload or {}),
        "response_recovery": {
            **dict((job.payload or {}).get("response_recovery") or {}),
            "status": "processed",
            "processed_at": processed_at.isoformat(),
            "model_called": True,
        },
    }


async def _latest_response(
    session: AsyncSession, job: Job
) -> JobTextResponse | None:
    return await session.scalar(
        select(JobTextResponse)
        .where(JobTextResponse.job_id == job.id)
        .order_by(JobTextResponse.received_at.desc(), JobTextResponse.id.desc())
        .limit(1)
    )


async def _migrate_legacy_payload_response(
    session: AsyncSession, job: Job
) -> JobTextResponse | None:
    recovery = dict((job.payload or {}).get("response_recovery") or {})
    text = recovery.get("text")
    if not isinstance(text, str):
        return None
    received_at = utcnow()
    from app.services.execution_policy_service import job_text_response_retention_days
    stored = JobTextResponse(
        job_id=job.id,
        call_id=recovery.get("call_id"),
        attempt=int(recovery.get("attempt") or job.attempts),
        response_text=text,
        response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        response_chars=len(text),
        usage=dict(recovery.get("usage") or {}),
        streaming=bool(recovery.get("streaming")),
        status="available",
        received_at=received_at,
        expires_at=received_at + timedelta(days=job_text_response_retention_days(job)),
    )
    session.add(stored)
    await session.flush()
    job.payload = {
        **(job.payload or {}),
        "response_recovery": {
            "schema_version": 2,
            "response_id": stored.id,
            "call_id": stored.call_id,
            "attempt": stored.attempt,
            "received_at": stored.received_at.isoformat(),
            "expires_at": stored.expires_at.isoformat(),
            "status": "available",
        },
    }
    return stored


async def reprocess_preserved_response(
    session: AsyncSession,
    job: Job,
    actor_id: int,
    *,
    character_name_corrections: dict[str, str] | None = None,
    expected_response_sha256: str | None = None,
) -> Job:
    """Re-run local business processing only; this function has no provider path."""
    from app.core.retired_workflows import require_active_workflow
    require_active_workflow(job.target_type)
    if job.status != JOB_STATUS_FAILED:
        raise ConflictError("仅本地处理失败的任务可以重新处理已保存响应")
    if job.error_code != "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED":
        raise ConflictError("该任务没有可重新处理的已保存模型响应")
    if character_name_corrections is not None and not ((job.payload or {}).get("parameters") or {}).get("long_form_work_id"):
        raise ConflictError("角色对应仅用于分批大纲结果恢复")
    stored = await _latest_response(session, job)
    if stored is None and job.target_type not in {"episode_content_analysis", "episode_content_detail"}:
        stored = await _migrate_legacy_payload_response(session, job)
    if stored is None:
        raise ConflictError("已保存模型响应不存在或已清理，不能执行本地重处理")
    if stored.expires_at.replace(tzinfo=None) <= utcnow().replace(tzinfo=None):
        stored.status = "expired"
        raise ConflictError("已保存模型响应已超过保留期限，不能执行本地重处理")
    if job.target_type in {"episode_content_analysis", "episode_content_detail"}:
        submission = (job.payload or {}).get("text_submission") or {}
        if (hashlib.sha256(stored.response_text.encode("utf-8")).hexdigest() != stored.response_sha256
                or stored.response_sha256 != submission.get("response_sha256")
                or stored.call_id != submission.get("call_id")):
            raise ConflictError("已保存响应的完整性校验失败，不能恢复或覆盖计划")

    corrections = None
    if character_name_corrections is not None:
        from app.services.outline_cast_recovery import validate_corrections
        corrections = await validate_corrections(
            session, job, stored, character_name_corrections, expected_response_sha256
        )

    claimed = await session.execute(update(Job).where(
        Job.id == job.id, Job.status == JOB_STATUS_FAILED,
    ).values(status="processing"))
    if claimed.rowcount != 1:
        raise ConflictError("任务已开始恢复或状态已变化，请刷新后查看")

    recovery_worker = f"local-recovery-{actor_id}-{uuid4().hex[:8]}"
    recovery_job_id = job.id
    result = {
        "text": stored.response_text,
        "usage": dict(stored.usage or {}),
        "streaming": stored.streaming,
    }
    if corrections:
        result["_outline_cast_corrections"] = corrections
    try:
        async with session.begin_nested():
            if job.target_type == "episode_script_generation":
                from app.services.creation_script_generation_service import validate_episode_script_job_sources
                await validate_episode_script_job_sources(session, job, require_parent_active=False)
                parent = await session.get(Job, job.parent_job_id) if job.parent_job_id else None
                if (job.resolution or {}).get("status") in {"superseded", "replacement_pending"}:
                    raise ConflictError("本集任务已被替代，不能恢复旧结果")
                if parent is not None:
                    if parent.status not in {"failed", "processing"}:
                        raise ConflictError("该正文批次已取消或结束，不能恢复旧结果")
                    newer = await session.scalar(select(Job.id).where(
                        Job.project_id == job.project_id, Job.target_type == "episode_script_batch",
                        Job.id > parent.id,
                    ).limit(1))
                    if newer is not None:
                        raise ConflictError("已有后续正文批次，请在最新批次恢复结果")
                    parent.status = "processing"
                    parent.finished_at = None
                    parent.error_code = None
                    parent.error_message = None
            if job.target_type == "script_asset_breakdown_batch":
                from app.services.creation_breakdown_sources import (
                    validate_script_asset_breakdown_job_sources,
                )

                item = await validate_script_asset_breakdown_job_sources(
                    session, job, require_parent_active=False
                )
                parent = await session.get(Job, job.parent_job_id)
                progress = dict((item.settings or {}).get("asset_breakdown") or {})
                if (
                    parent is None
                    or parent.status not in {"failed", "processing"}
                    or progress.get("parent_job_id") != parent.id
                    or (job.resolution or {}).get("status") in {"superseded", "replacement_pending"}
                ):
                    raise ConflictError("该拆解批次已取消、被替代或不再是当前版本，不能恢复旧结果")
                parent.status = "processing"
                parent.finished_at = None
                parent.error_code = None
                parent.error_message = None
            job.status = "processing"
            job.worker_id = recovery_worker
            from app.core.config import settings
            job.lease_expires_at = utcnow() + timedelta(seconds=settings.job_lease_seconds)
            job.finished_at = None
            stored.status = "processing"
            stored.processing_attempts += 1
            await session.flush()
            from app.services.job_state_result_service import mark_succeeded
            from app.services.job_text_finalize_service import finalize_text_result

            persisted = await finalize_text_result(session, job, result)
            persisted = {
                **persisted,
                "_response_recovery": {
                    "response_id": stored.id,
                    "reprocessed_locally": True,
                    "model_called": False,
                    "character_name_corrections": corrections,
                    "actor_id": actor_id,
                    "response_sha256": stored.response_sha256,
                },
            }
            if not await mark_succeeded(
                session, job.id, recovery_worker, persisted
            ):
                raise ConflictError("任务状态已变化，本地重处理未写入")
            stored.status = "processed"
            stored.processed_at = utcnow()
            stored.last_error_code = None
            stored.last_error_message = None
            job.payload = {
                **(job.payload or {}),
                "response_recovery": {
                    **dict((job.payload or {}).get("response_recovery") or {}),
                    "status": "processed",
                    "processed_at": stored.processed_at.isoformat(),
                    "model_called": False,
                    "character_name_corrections": corrections,
                    "actor_id": actor_id,
                    "response_sha256": stored.response_sha256,
                },
            }
    except Exception as error:
        if isinstance(error, AppError):
            exc = error
        else:
            from app.core.job_failure import local_exception
            logging.getLogger(__name__).exception("任务 %s 本地恢复异常", recovery_job_id)
            code, message, details = local_exception(error)
            exc = AppError(message, details=details)
            exc.code = code
        from app.services.job_state_result_service import (
            aggregate_parent_job,
            safe_output_diagnostic,
        )

        await session.refresh(job)
        await session.refresh(stored)
        stored.status = "available"
        stored.processing_attempts += 1
        stored.last_error_code = exc.code
        stored.last_error_message = exc.message[:2000]
        recovery = {
            **dict((job.payload or {}).get("response_recovery") or {}),
            "status": "available",
            "last_reprocess_error_code": exc.code,
            "last_reprocess_error_message": exc.message[:500],
            "model_called": False,
        }
        output_diagnostic = safe_output_diagnostic(exc.details)
        if output_diagnostic:
            recovery["output_diagnostic"] = output_diagnostic
        job.status = JOB_STATUS_FAILED
        job.worker_id = None
        job.finished_at = utcnow()
        job.error_code = "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
        job.error_message = (
            f"模型结果已保存，但本地处理仍失败：{exc.message}"
        )
        job.payload = {
            **(job.payload or {}),
            "response_recovery": recovery,
        }
        await session.flush()
        await session.refresh(job)
        await aggregate_parent_job(session, job.id)
    await session.flush()
    return job
