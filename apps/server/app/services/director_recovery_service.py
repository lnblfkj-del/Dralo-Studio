"""One-page director recovery: no provider calls before scoped confirmation."""

from hashlib import sha256
import json
from datetime import datetime

from sqlalchemy import select, update

from app.core.errors import AppError, ConflictError
from app.models import Job, Provider, ProviderModel, utcnow
from app.services import episode_director_pipeline_service as pipeline

TERMINAL = {"failed", "cancelled", "succeeded"}


def _timestamp(value):
    return value.replace(tzinfo=None).isoformat() if value is not None else None


def _configuration(model, provider):
    # Ignore adaptive concurrency counters: successful sibling calls must not
    # invalidate a fee confirmation, while actual routing/config changes must.
    return sha256(json.dumps({
        "model": model.model_id, "enabled": model.enabled,
        "protocol": model.api_protocol, "url": model.api_base_url,
        "params": model.default_params, "pricing": model.pricing,
        "provider": provider.id, "base_url": provider.base_url,
        "provider_protocol": provider.protocol, "key": provider.api_key_ciphertext,
        "provider_enabled": provider.enabled,
    }, sort_keys=True, default=str).encode()).hexdigest()


async def parent_job(session, job):
    parent = job
    if job.target_type in {pipeline.TARGET_OUTLINE, pipeline.TARGET_SEGMENT}:
        parent = await session.get(Job, job.parent_job_id)
    if parent is None or parent.target_type != pipeline.TARGET_PIPELINE:
        raise ConflictError("导演流水线父任务不存在")
    return parent


async def _validate(session, parent, actor_id):
    from app.services.team_access import same_team
    from app.services.episode_auto_planning_service import validate_sources

    if not await same_team(session, parent.owner_id, actor_id):
        raise ConflictError("无权恢复该导演任务")
    if parent.deleted_at or parent.status != "failed" or parent.resolution:
        raise ConflictError("任务已结束、取消、删除或正在处理，请读取最新状态")
    newer = await session.scalar(select(Job.id).where(
        Job.target_id == parent.target_id,
        Job.target_type.in_({pipeline.TARGET_PIPELINE, "episode_director_plan"}),
        Job.id > parent.id, Job.deleted_at.is_(None),
    ).limit(1))
    if newer:
        raise ConflictError("已有后续规划任务，请使用最新任务")
    await validate_sources(session, parent)


async def _children(session, parent):
    return pipeline._usable_children(list((await session.scalars(
        select(Job).where(Job.parent_job_id == parent.id).order_by(Job.id)
        .execution_options(populate_existing=True)
    )).all()))


async def recovery_state(session, job, actor_id):
    from app.services.job_text_response_service import _latest_response

    parent = await parent_job(session, job)
    children = await _children(session, parent)
    result = {
        "job_id": parent.id, "completed_segments": sum(
            child.target_type == pipeline.TARGET_SEGMENT and child.status == "succeeded"
            for child in children
        ),
        "expected_segments": int((parent.payload or {}).get("expected_segments") or 0),
        "free_job_ids": [], "paid_scopes": [], "blocked_scopes": [],
        "can_retry": False, "confirmation_token": None,
        "max_new_calls": 0, "unknown_result_count": 0,
        "fee_message": "费用按渠道实际计费，当前无法预估；上次失败的调用也可能已计费。",
        "block_reason": None,
    }
    try:
        await _validate(session, parent, actor_id)
        if any(child.status not in TERMINAL for child in children):
            raise ConflictError("仍有任务正在执行或取消中，请等待当前任务结束")
    except AppError as exc:
        result["block_reason"] = exc.message
        return result
    fingerprint = []
    for child in children:
        stored = await _latest_response(session, child)
        recovery = (child.payload or {}).get("response_recovery") or {}
        submission = (child.payload or {}).get("text_submission") or {}
        fingerprint.append({
            "id": child.id, "status": child.status, "deleted": _timestamp(child.deleted_at),
            "attempts": child.attempts, "updated": _timestamp(child.updated_at),
            "payload": child.payload, "response": stored.response_sha256 if stored else None,
            "expires": _timestamp(stored.expires_at) if stored else None,
            "processing_attempts": stored.processing_attempts if stored else None,
        })
        if child.status not in {"failed", "cancelled"}:
            continue
        scope = {"job_id": child.id, "label": (
            f"片段 {(child.payload or {}).get('director_pipeline', {}).get('segment_order')}"
            if child.target_type == pipeline.TARGET_SEGMENT else "片段边界规划"
        ), "provider": child.provider, "model": child.model}
        if child.deleted_at:
            result["blocked_scopes"].append({**scope, "reason": "失败子任务已删除，请先恢复任务"})
            continue
        unexpired = stored is not None and stored.expires_at.replace(tzinfo=None) > utcnow().replace(tzinfo=None)
        attempted = bool(recovery.get("last_reprocess_error_code")) or bool(stored and stored.processing_attempts)
        if unexpired and not attempted and child.error_code == "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED":
            result["free_job_ids"].append(child.id)
            continue
        detail = child.failure_detail or {}
        provider_reason = str(child.error_message or "").lower()
        quota_blocked = any(word in provider_reason for word in (
            "insufficient_quota", "quota_exceeded", "quota exhausted", "no available", "余额不足", "额度不足",
        ))
        if detail.get("action") == "none" or detail.get("http_status") in {401, 402, 403} or quota_blocked:
            result["blocked_scopes"].append({**scope, "reason": detail.get("hint") or child.error_message or "请修正渠道配置后重试"})
            continue
        model = await session.get(ProviderModel, (child.payload or {}).get("provider_model_id"))
        provider = await session.get(Provider, child.provider_id)
        if model is None or provider is None or not model.enabled or not provider.enabled:
            result["blocked_scopes"].append({**scope, "reason": "文本模型或渠道已停用，请修正配置"})
            continue
        if model.model_id != child.model:
            result["blocked_scopes"].append({**scope, "reason": "原文本模型标识已修改，请重新规划，避免静默切换模型"})
            continue
        unknown = submission.get("status") == "submitted" and not submission.get("response_received")
        from app.services.pricing_service import estimate
        quote = estimate(model, (child.payload or {}).get("prompt", ""), (child.payload or {}).get("parameters"))
        result["paid_scopes"].append({**scope, "unknown_result": unknown, "pricing_estimate": quote})
        result["unknown_result_count"] += int(unknown)
        fingerprint.append({"configuration": _configuration(model, provider)})
    parent_recovery = (parent.payload or {}).get("response_recovery") or {}
    if (parent.error_code == "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
            and parent_recovery.get("kind") == "director_child_results"):
        if not parent_recovery.get("last_reprocess_error_code"):
            result["free_job_ids"].append(parent.id)
        else:
            result["block_reason"] = "已有结果仍无法汇总，请核对正文或制作资料；重复调用片段模型不能解决本地写入问题。"
    result["max_new_calls"] = len(result["paid_scopes"])
    if result["paid_scopes"] and all(
        scope["pricing_estimate"].get("amount") is not None for scope in result["paid_scopes"]
    ):
        result["fee_message"] = "以上按配置价格预估，不是扣费上限或渠道账单；上次失败的调用也可能已计费。"
    elif any(scope["pricing_estimate"].get("amount") is not None for scope in result["paid_scopes"]):
        result["fee_message"] = "部分范围无法预估，已列价格仅为估算；费用按渠道实际计费，上次失败的调用也可能已计费。"
    if result["paid_scopes"] and not result["free_job_ids"]:
        from app.services.episode_director_service import get_video_capabilities, _fingerprint
        execution = parent.payload["director_execution"]
        current = await get_video_capabilities(session, execution["video_model_id"])
        if _fingerprint(current) != _fingerprint(execution["video_model_capability_snapshot"]):
            result["block_reason"] = "目标视频模型能力已变化，请重新规划；不能混用旧边界继续付费生成。"
    result["can_retry"] = bool(result["free_job_ids"] or result["paid_scopes"]) and not result["block_reason"]
    if result["paid_scopes"] and not result["free_job_ids"] and not result["block_reason"]:
        result["confirmation_token"] = sha256(json.dumps({
            "actor": actor_id, "parent_id": parent.id, "payload": parent.payload,
            "updated": _timestamp(parent.updated_at), "children": fingerprint,
            "scopes": result["paid_scopes"],
        }, sort_keys=True, default=str).encode()).hexdigest()
    return result


async def _claim(session, parent):
    # Conditional write works on SQLite as well as PostgreSQL; a second request
    # cannot use a snapshot taken before the first recovery transaction.
    claimed = await session.execute(update(Job).where(
        Job.id == parent.id, Job.status == "failed", Job.updated_at == parent.updated_at,
    ).values(updated_at=utcnow()))
    if claimed.rowcount != 1:
        raise ConflictError("任务已开始恢复或状态已变化，请读取最新状态")
    await session.refresh(parent)


async def recover_saved(session, job, actor_id):
    from app.services.job_text_response_service import reprocess_preserved_response

    parent = await parent_job(session, job)
    await _claim(session, parent)
    state = await recovery_state(session, parent, actor_id)
    if state["block_reason"]:
        raise ConflictError(state["block_reason"])
    for child_id in state["free_job_ids"]:
        child = await session.get(Job, child_id)
        if child.target_type == pipeline.TARGET_OUTLINE:
            child.payload = {**child.payload, "require_segment_confirmation": True}
        await reprocess_preserved_response(session, child, actor_id)
    await session.flush()
    return parent


async def confirm_paid(session, job, actor_id, *, confirmation_token,
                       channel_checked=False, accept_unknown_charge=False, reason):
    parent = await parent_job(session, job)
    # Acquire a write lock without changing the fingerprint until it is checked.
    claimed = await session.execute(update(Job).where(
        Job.id == parent.id, Job.status == "failed", Job.updated_at == parent.updated_at,
    ).values(updated_at=parent.updated_at))
    if claimed.rowcount != 1:
        raise ConflictError("任务状态已变化，请重新核对失败范围")
    await session.refresh(parent)
    state = await recovery_state(session, parent, actor_id)
    if (not confirmation_token or confirmation_token != state["confirmation_token"]
            or not state["max_new_calls"]):
        raise ConflictError(state["block_reason"] or "失败范围或配置已变化，请重新核对并确认")
    if state["unknown_result_count"] and not (channel_checked or accept_unknown_charge):
        raise ConflictError("上次请求结果未知，可能已计费；请核对渠道或明确接受重复计费风险")
    recalled_at = utcnow().isoformat()
    for scope in state["paid_scopes"]:
        child = await session.get(Job, scope["job_id"])
        model = await session.get(ProviderModel, child.payload["provider_model_id"])
        provider = await session.get(Provider, child.provider_id)
        child.status = "queued"
        child.progress = child.attempts = 0
        child.max_attempts = 1
        child.available_at = child.started_at = child.finished_at = None
        child.worker_id = child.lease_expires_at = None
        child.error_code = child.error_message = None
        child.result = None
        child.payload = {**child.payload, "text_submission": {}, "response_recovery": {},
                         "require_segment_confirmation": True,
                         "recovery": {"kind": "confirmed_paid_recall", "confirmed_by": actor_id,
                                      "confirmed_at": recalled_at, "reason": reason,
                                      "pricing_snapshot": scope["pricing_estimate"],
                                      "configuration": _configuration(model, provider)}}
    history = list((parent.payload or {}).get("recall_history") or [])
    history.append({"child_job_ids": [item["job_id"] for item in state["paid_scopes"]],
                    "confirmed_by": actor_id, "confirmed_at": recalled_at,
                    "reason": reason, "max_new_calls": state["max_new_calls"],
                    "confirmation_token": confirmation_token, "channel_checked": channel_checked,
                    "accepted_unknown_charge": accept_unknown_charge, "scopes": state["paid_scopes"]})
    parent.payload = {**parent.payload, "recall_history": history, "recovery_summary": {}}
    parent.status = "processing"
    parent.finished_at = None
    parent.error_code = parent.error_message = None
    await session.flush()
    return parent


async def validate_execution(session, job, model, provider):
    """Reject stale queued retries before billing or provider submission."""
    from app.services.episode_auto_planning_service import validate_sources
    parent = await parent_job(session, job)
    if parent.status != "processing" or parent.deleted_at or parent.resolution:
        raise ConflictError("导演任务已结束或取消，未提交新的模型请求")
    newer = await session.scalar(select(Job.id).where(
        Job.target_id == parent.target_id, Job.id > parent.id, Job.deleted_at.is_(None),
        Job.target_type.in_({pipeline.TARGET_PIPELINE, "episode_director_plan"}),
    ).limit(1))
    if newer:
        raise ConflictError("已有后续规划任务，未提交旧任务的模型请求")
    await validate_sources(session, parent)
    expected = (job.payload or {}).get("recovery", {}).get("configuration")
    if expected and expected != _configuration(model, provider):
        raise ConflictError("确认后模型配置已变化，未提交新的模型请求，请重新核对范围和费用")


async def latest_attempt(session, parent, children):
    from app.models import BillingCall
    history = (parent.payload or {}).get("recall_history") or []
    if not history:
        return None
    latest = history[-1]
    try:
        since = datetime.fromisoformat(latest["confirmed_at"]).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        return None
    calls = list((await session.scalars(select(BillingCall).where(
        BillingCall.job_id.in_(latest["child_job_ids"]), BillingCall.created_at >= since,
    ))).all())
    submitted_ids = {
        (child.payload or {}).get("text_submission", {}).get("call_id")
        for child in children if (child.payload or {}).get("text_submission", {}).get("status")
        in {"submitted", "response_received"}
    }
    return {
        "max_new_calls": latest.get("max_new_calls", len(latest["child_job_ids"])),
        "submitted_calls": sum(bool(call.meter.get("returned") or call.state == "outcome_unknown"
                                    or call.id in submitted_ids) for call in calls),
        "fees": [{"currency": call.currency, "amount": call.amount, "reason": call.reason}
                 for call in calls if call.state != "not_submitted"],
        "fee_message": "用量核算不是渠道确认扣费；未取得账单的费用仍待核对。",
    }
