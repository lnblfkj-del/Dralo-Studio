"""Job result persistence, failure handling and parent aggregation."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DOWNLOADING,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    EpisodeProduction,
    Job,
    Shot,
    VideoSegment,
    utcnow,
)
from app.services.job_concurrency_service import BATCH_PARENT_TARGETS
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED
from app.services.job_authorization_service import execution_authorized

_PROVIDER_DIAGNOSTIC_KEYS = {
    "http_status",
    "request_id",
    "provider_error_code",
    "provider_error_message",
    "endpoint_host",
    "endpoint_path",
    "retry_after_seconds",
}
_OUTPUT_DIAGNOSTIC_KEYS = {
    "input_chars",
    "first_non_whitespace",
    "last_non_whitespace",
    "top_level_closed",
    "prefix_chars",
    "trailing_chars",
    "json_error_position",
    "json_error_line",
    "json_error_column",
    "scan_error",
    "fenced",
    "invalid_fields",
    "field_errors",
    "expected_episode_numbers",
    "actual_episode_numbers",
    "unexpected_result_groups",
    "finish_reason",
    "stream_terminal_seen",
    "output_budget_tokens",
}


def _safe_provider_diagnostic(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    diagnostic: dict[str, Any] = {}
    for key in _PROVIDER_DIAGNOSTIC_KEYS:
        value = details.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            diagnostic[key] = value
        elif isinstance(value, str) and value and len(value) <= 300:
            diagnostic[key] = value
    return diagnostic


def safe_output_diagnostic(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    diagnostic: dict[str, Any] = {}
    for key in _OUTPUT_DIAGNOSTIC_KEYS:
        value = details.get(key)
        if key == "field_errors" and isinstance(value, list):
            diagnostic[key] = [
                {k: str(item[k])[:180] for k in ("field", "label", "reason", "expected", "actual_type") if k in item}
                for item in value[:12] if isinstance(item, dict)
            ]
        elif isinstance(value, bool):
            diagnostic[key] = value
        elif isinstance(value, int):
            diagnostic[key] = value
        elif isinstance(value, str) and value and len(value) <= 100:
            diagnostic[key] = value
        elif isinstance(value, list) and len(value) <= 20 and all(
            isinstance(item, (int, str)) and len(str(item)) <= 100 for item in value
        ):
            diagnostic[key] = value
    return diagnostic


async def _resolve_superseded_failures(session: AsyncSession, succeeded: Job) -> None:
    """Keep failed history, but remove failures already replaced by this success from attention."""
    if succeeded.target_type is None or succeeded.target_id is None:
        return
    previous = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.id < succeeded.id,
                    Job.project_id == succeeded.project_id,
                    Job.job_type == succeeded.job_type,
                    Job.target_type == succeeded.target_type,
                    Job.target_id == succeeded.target_id,
                    Job.status == JOB_STATUS_FAILED,
                )
            )
        ).all()
    )
    resolved_at = utcnow().isoformat()
    succeeded_parameters = dict((succeeded.payload or {}).get("parameters") or {})
    succeeded_pipeline = dict((succeeded.payload or {}).get("director_pipeline") or {})
    succeeded_scope_key = str(
        succeeded_parameters.get("scope_key")
        or succeeded_pipeline.get("segment_key")
        or ""
    )
    replaces_job_id = int(
        dict((succeeded.payload or {}).get("recovery") or {}).get("replaces_job_id") or 0
    )
    for item in previous:
        item_parameters = dict((item.payload or {}).get("parameters") or {})
        item_pipeline = dict((item.payload or {}).get("director_pipeline") or {})
        item_scope_key = str(
            item_parameters.get("scope_key")
            or item_pipeline.get("segment_key")
            or ""
        )
        if succeeded_scope_key and item_scope_key != succeeded_scope_key and item.id != replaces_job_id:
            continue
        item.payload = {
            **(item.payload or {}),
            "resolution": {
                "status": "superseded",
                "by_job_id": succeeded.id,
                "resolved_at": resolved_at,
            },
        }


async def mark_succeeded(
    session: AsyncSession, job_id: int, worker_id: str, result_data: dict[str, Any]
) -> bool:
    result = await session.execute(
        update(Job).execution_options(synchronize_session="fetch")
        .where(execution_authorized())
        .where(
            Job.id == job_id,
            Job.worker_id == worker_id,
            Job.lease_expires_at > utcnow(),
            Job.status.in_([
                JOB_STATUS_RUNNING,
                JOB_STATUS_PROCESSING,
                JOB_STATUS_DOWNLOADING,
            ]),
        )
        .values(
            status=JOB_STATUS_SUCCEEDED,
            progress=100,
            result=result_data,
            error_code=None,
            error_message=None,
            worker_id=None,
            lease_expires_at=None,
            available_at=None,
            finished_at=utcnow(),
        )
    )
    if not result.rowcount:
        return False
    succeeded = await session.get(Job, job_id)
    if succeeded is not None:
        await _resolve_superseded_failures(session, succeeded)
    from app.services.job_concurrency_service import record_model_success
    await record_model_success(session, job_id)
    await aggregate_parent_job(session, job_id)
    return True


async def mark_failed(
    session: AsyncSession,
    job_id: int,
    worker_id: str,
    code: str,
    message: str,
    *,
    retry_after_seconds: int | None = None,
    provider_diagnostic: dict[str, Any] | None = None,
) -> bool:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.worker_id == worker_id))
    if job is None or job.status not in {
        JOB_STATUS_RUNNING,
        JOB_STATUS_PROCESSING,
        JOB_STATUS_DOWNLOADING,
    }:
        return False
    job.worker_id = None
    job.lease_expires_at = None
    submission = dict((job.payload or {}).get("text_submission") or {})
    response_received = bool(submission.get("response_received"))
    original_code, original_message = code, message
    if response_received:
        code = "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
        message = f"模型响应已保存，处理失败：{message}"
        if original_code == "MODEL_OUTPUT_TRUNCATED" and job.job_type == "text":
            message = f"模型响应不完整，已保存收到的内容：{original_message}。本地重新处理不能补全缺失部分。"
        if original_code == "MODEL_OUTPUT_TRUNCATED" and job.target_type == "script_asset_breakdown_batch":
            usage = dict(submission.get("usage") or {})
            budget = (job.execution_policy_snapshot.get("text_model") or {}).get("effective_output_tokens")
            used = usage.get("completion_tokens")
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            detail = f"（输出用量 {used} / 预算 {budget} Token" if used and budget else ""
            if detail:
                detail += f"，其中推理 {reasoning} Token）" if reasoning is not None else "）"
            message = (
                f"模型输出被截断{detail}，已保存原始响应但资产数据不完整。"
                "本地重新处理不能补全缺失内容，请确认后按更小范围重新生成；成功批次已保留。"
            )
    elif submission.get("status") == "submitted" and code in {
        "PROVIDER_ERROR",
        "PROVIDER_ENDPOINT_ERROR",
        "PROVIDER_MODEL_NOT_FOUND",
        "PROVIDER_METHOD_NOT_ALLOWED",
        "TIMEOUT_ERROR",
        "GENERATION_FAILED",
    }:
        diagnostic = _safe_provider_diagnostic(provider_diagnostic)
        if diagnostic.get("http_status"):
            message = (
                f"渠道返回 HTTP {diagnostic['http_status']}：{original_message}；"
                "未获得可用生成结果，可手动重试"
            )
            if diagnostic.get("request_id"):
                message += f"（请求 ID：{diagnostic['request_id']}）"
        else:
            code = "PROVIDER_OUTCOME_UNKNOWN"
            message = f"{original_message}；上游是否完成尚不确定，手动重试将发起新请求"
    elif (job.job_type == "tts"
            and (job.payload or {}).get("media_submission", {}).get("started")
            and code in {"PROVIDER_ERROR", "TIMEOUT_ERROR", "GENERATION_FAILED"}):
        code = "PROVIDER_OUTCOME_UNKNOWN"
        message = "配音请求已提交但结果未确认，请核对渠道记录；系统不会自动重发"
    job.error_code = code
    job.error_message = message
    history = list((job.payload or {}).get("attempt_history") or [])
    history_item = {
        "attempt": job.attempts,
        "call_id": submission.get("call_id"),
        "stage": "local_processing" if response_received else "provider",
        "response_received": response_received,
        "error_code": original_code,
        "error_message": original_message,
        "recorded_at": utcnow().isoformat(),
    }
    diagnostic = _safe_provider_diagnostic(provider_diagnostic)
    if retry_after_seconds:
        diagnostic["retry_after_seconds"] = retry_after_seconds
    if diagnostic:
        history_item["provider_diagnostic"] = diagnostic
    output_diagnostic = safe_output_diagnostic(provider_diagnostic)
    if output_diagnostic:
        history_item["output_diagnostic"] = output_diagnostic
    history.append(history_item)
    job.payload = {**(job.payload or {}), "attempt_history": history[-20:]}
    from app.core.video_submission import failed_before_video_generation, normalize_rejected_video_submission
    if job.job_type == "video":
        job.payload = normalize_rejected_video_submission(job.payload)
    if job.job_type == "video" and failed_before_video_generation(diagnostic):
        video_submission = dict(job.payload.get("video_submission") or {})
        video_submission.pop("id", None)
        video_submission.pop("business_id", None)
        job.payload = {
            **job.payload,
            "video_submission": {
                **video_submission,
                "started": False,
                "pre_submit_failure": diagnostic,
            },
        }
    model_cooldown = None
    if code == "RATE_LIMIT_ERROR":
        from app.services.job_concurrency_service import record_model_rate_limit
        model_cooldown = await record_model_rate_limit(
            session, job, retry_after_seconds=retry_after_seconds
        )
    uncertain_video = job.payload.get("video_submission", {}).get("started") and not job.payload.get("video_submission", {}).get("id")
    if uncertain_video:
        job.error_message = message + "；未确认外部任务 ID，请先在当前模型渠道核对任务/账单，系统不会自动重发"
    non_retryable = code in {
        "WORKSPACE_ACCESS_REVOKED",
        "MODEL_ROUTE_CHANGED",
        "PROVIDER_PARAMETER_ERROR",
        "PROVIDER_AUTH_ERROR",
        "PROVIDER_ENDPOINT_ERROR",
        "PROVIDER_MODEL_NOT_FOUND",
        "PROVIDER_METHOD_NOT_ALLOWED",
        "QUOTA_ERROR",
        "CONFLICT",
        "MODEL_NOT_FOUND",
        "VIDEO_REMOTE_TIMEOUT",
        "PROVIDER_OUTCOME_UNKNOWN",
        "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED",
    }
    manual_text_failure = job.job_type == "text" and submission.get("status") == "submitted"
    if non_retryable or manual_text_failure or job.attempts >= job.max_attempts or uncertain_video:
        job.status = JOB_STATUS_FAILED
        job.finished_at = utcnow()
    else:
        job.status = JOB_STATUS_RETRYING
        from app.services.execution_policy_service import job_retry_backoff_seconds
        retry_at = utcnow() + timedelta(seconds=job_retry_backoff_seconds(job))
        job.available_at = max(retry_at, model_cooldown) if model_cooldown else retry_at
    await session.flush()
    if job.target_type == "shot" and job.target_id is not None:
        shot = await session.get(Shot, job.target_id)
        if shot is not None and shot.status != SHOT_STATUS_SUPERSEDED:
            shot.status = "failed" if job.status == JOB_STATUS_FAILED else "generating"
    if job.target_type == "video_segment" and job.target_id is not None:
        segment = await session.get(VideoSegment, job.target_id)
        if segment is not None:
            segment.status = "failed" if job.status == JOB_STATUS_FAILED else "generating"
        if job.status == JOB_STATUS_FAILED:
            production = await session.scalar(
                select(EpisodeProduction).where(
                    EpisodeProduction.episode_id == (job.payload or {}).get("episode_id")
                )
            )
            if production is not None:
                production.last_error = job.error_message
    if job.target_type == "episode_export" and job.target_id is not None:
        production = await session.scalar(
            select(EpisodeProduction).where(
                EpisodeProduction.episode_id == job.target_id
            )
        )
        if production is not None:
            production.last_error = job.error_message
    if job.status == JOB_STATUS_FAILED:
        await aggregate_parent_job(session, job.id)
    return True


async def aggregate_parent_job(session: AsyncSession, child_job_id: int) -> Job | None:
    """根据子任务状态更新批量父任务；父任务本身永远不进入 Worker 队列。"""
    child = await session.get(Job, child_job_id)
    if child is None or child.parent_job_id is None:
        return None
    parent = await session.get(Job, child.parent_job_id)
    if parent is None or parent.target_type not in BATCH_PARENT_TARGETS:
        return None
    if parent.target_type == "episode_director_pipeline":
        from app.services.episode_director_pipeline_service import aggregate_parent

        return await aggregate_parent(session, parent)
    all_children = list((await session.scalars(
        select(Job).where(Job.parent_job_id == parent.id)
    )).all())
    children = [
        item
        for item in all_children
        if (item.resolution or {}).get("status") not in {
            "superseded",
            "replacement_pending",
        }
    ]
    is_sequential_script_batch = parent.target_type == "episode_script_batch"
    expected_total = int((parent.payload or {}).get("expected_total") or 0)
    total = max(len(children), expected_total) if expected_total else len(children)
    succeeded = sum(item.status == JOB_STATUS_SUCCEEDED for item in children)
    failed = sum(item.status == JOB_STATUS_FAILED for item in children)
    cancelled = sum(item.status == JOB_STATUS_CANCELLED for item in children)
    completed = succeeded + failed + cancelled
    failed_children = [
        item
        for item in children
        if item.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
    ]
    local_reprocess_pending = 0
    local_reprocess_exhausted = 0
    channel_check_required = 0
    ordinary_retry_required = 0
    for item in failed_children:
        item_payload = dict(item.payload or {})
        submission = dict(item_payload.get("text_submission") or {})
        recovery = dict(item_payload.get("response_recovery") or {})
        action = (item.failure_detail or {}).get("action")
        if action == "retry":
            ordinary_retry_required += 1
        elif submission.get("response_received"):
            if recovery.get("last_reprocess_error_code") or item.asset_response_truncated:
                local_reprocess_exhausted += 1
            else:
                local_reprocess_pending += 1
        elif submission.get("status") == "submitted":
            channel_check_required += 1
        else:
            ordinary_retry_required += 1
    paid_recall_allowed = bool(failed_children) and all((
        local_reprocess_pending == 0,
        ordinary_retry_required == 0,
        local_reprocess_exhausted + channel_check_required == len(failed_children),
    ))
    recovery_summary = {
        "failed_scopes": len(failed_children),
        "local_reprocess_required": local_reprocess_pending,
        "local_reprocess_pending": local_reprocess_pending,
        "local_reprocess_exhausted": local_reprocess_exhausted,
        "channel_check_required": channel_check_required,
        "ordinary_retry_required": ordinary_retry_required,
        "paid_recall_allowed": paid_recall_allowed,
    }
    parent.payload = {
        **(parent.payload or {}),
        "recovery_summary": recovery_summary,
    }
    actual_generation_duration = sum(
        float((item.result or {}).get("duration") or 0)
        for item in children
        if item.status == JOB_STATUS_SUCCEEDED
    )
    usage_totals: dict[str, int] = {}
    for item in children:
        usage = (item.result or {}).get("usage") or {}
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                usage_totals[key] = usage_totals.get(key, 0) + value
    first_visible_candidates = []
    for item in children:
        metrics = (item.result or {}).get("_execution_metrics") or {}
        if not isinstance(metrics, dict) or metrics.get("first_visible_ms") is None:
            continue
        created_offset = max(
            0,
            round((
                item.created_at.replace(tzinfo=None)
                - parent.created_at.replace(tzinfo=None)
            ).total_seconds() * 1000),
        )
        first_visible_candidates.append(
            created_offset + int(metrics["first_visible_ms"])
        )
    batch_elapsed_ms = max(
        0,
        round((
            (parent.finished_at or utcnow()).replace(tzinfo=None)
            - parent.created_at.replace(tzinfo=None)
        ).total_seconds() * 1000),
    )
    parent.progress = round(completed * 100 / total) if total else 100
    parent.result = {
        **(parent.result or {}),
        "total": total,
        "completed": completed,
        "succeeded": succeeded,
        "failed": failed,
        "cancelled": cancelled,
        "actual_generation_duration": actual_generation_duration,
        "performance": {
            "schema_version": 1,
            "first_episode_visible_ms": (
                min(first_visible_candidates) if first_visible_candidates else None
            ),
            "batch_elapsed_ms": batch_elapsed_ms,
            "usage": usage_totals,
        },
        "children": [
            {
                "job_id": item.id,
                "segment_id": item.target_id if item.target_type == "video_segment" else None,
                "shot_id": item.target_id if item.target_type == "shot" else None,
                "asset_id": item.target_id if item.target_type == "asset" else None,
                "view_type": (item.payload or {}).get("view_type"),
                "view_label": (item.payload or {}).get("view_label"),
                "asset_generation_contract": (item.payload or {}).get("asset_generation_contract"),
                "segment_order": (item.payload or {}).get("segment_order"),
                "shot_ids": (item.payload or {}).get("shot_ids", []),
                "status": item.status,
                "progress": item.progress,
                "error_message": item.error_message,
                "media_file_id": (item.result or {}).get("media_file_id"),
                "episode_number": dict((item.payload or {}).get("parameters") or {}).get("episode_number"),
                "execution_metrics": (item.result or {}).get("_execution_metrics"),
                "usage": (item.result or {}).get("usage"),
            }
            for item in children
        ],
    }
    preparation_error = next((
        (item.result or {}).get("next_episode_preparation_error") for item in reversed(children)
        if item.status == JOB_STATUS_SUCCEEDED and (item.result or {}).get("next_episode_preparation_error")
    ), None)
    if is_sequential_script_batch and preparation_error and completed == len(children) and len(children) < total:
        parent.status = JOB_STATUS_FAILED
        parent.error_code = "NEXT_EPISODE_PREPARATION_FAILED"
        parent.error_message = (
            f"已保存 {succeeded}/{total} 集正文；下一集准备失败：{preparation_error['message']}。"
            "已完成正文不会回滚，可重试继续剩余分集。"
        )
        parent.finished_at = parent.finished_at or utcnow()
    elif is_sequential_script_batch and (failed or cancelled):
        blocking_child = next(
            (
                item
                for item in children
                if item.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
            ),
            None,
        )
        blocking_reason = (
            blocking_child.error_message
            if blocking_child is not None and blocking_child.error_message
            else "当前分集失败或已取消"
        )
        parent.status = JOB_STATUS_FAILED
        parent.error_code = "BATCH_PARTIAL_FAILURE" if succeeded else "BATCH_FAILED"
        parent.error_message = (
            f"分集正文完成 {succeeded}/{total}：{blocking_reason}；后续分集尚未提交"
        )
        parent.finished_at = parent.finished_at or utcnow()
    elif (
        parent.target_type == "script_asset_breakdown_group"
        and (failed or cancelled)
        and len(children) < total
        and completed == len(children)
    ):
        parent.status = JOB_STATUS_FAILED
        parent.error_code = "BATCH_PARTIAL_FAILURE" if succeeded else "BATCH_FAILED"
        parent.error_message = (
            f"剧本资产拆解完成 {succeeded}/{total}，视觉阶段有 {failed + cancelled} "
            "个范围失败，声音阶段尚未提交"
        )
        parent.finished_at = parent.finished_at or utcnow()
    elif completed == total and (failed or cancelled):
        parent.status = JOB_STATUS_FAILED
        parent.error_code = "BATCH_PARTIAL_FAILURE" if succeeded else "BATCH_FAILED"
        batch_label = {
            "script_study_batch_group": "剧本研读",
            "script_asset_breakdown_group": "剧本资产拆解",
            "episode_script_batch": "分集正文",
            "asset_image_batch": "批量资产图片",
            "segment_first_frame_batch": "批量片段首帧",
        }.get(parent.target_type, "批量视频")
        parent.error_message = f"{batch_label}完成 {succeeded}/{total}，其中 {failed + cancelled} 个任务失败或取消"
        parent.finished_at = parent.finished_at or utcnow()
    elif total and completed == total:
        parent.status = JOB_STATUS_SUCCEEDED
        parent.progress = 100
        parent.error_code = None
        parent.error_message = None
        parent.finished_at = parent.finished_at or utcnow()
        await _resolve_superseded_failures(session, parent)
    else:
        parent.status = JOB_STATUS_PROCESSING
        parent.finished_at = None
    if parent.target_type == "episode_video_batch" and parent.target_id is not None:
        production = await session.scalar(
            select(EpisodeProduction).where(
                EpisodeProduction.episode_id == parent.target_id
            )
        )
        if production is not None:
            production.last_error = (
                parent.error_message if parent.status == JOB_STATUS_FAILED else None
            )
    await session.flush()
    return parent
