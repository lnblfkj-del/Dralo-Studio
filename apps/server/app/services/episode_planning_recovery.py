"""Explicit, scoped recovery for frozen planning runs. No provider calls here."""

import hashlib
import json

from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models import JobTextResponse, utcnow
from app.models.episode_planning import EpisodePlanningRecord
from app.schemas.episode_references import SourceReferenceBinding
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_references import bound_analysis_prompt
from app.services.episode_planning_storage import read_content_analysis, read_plan
from app.services.job_query_service import get_job
from app.services.job_text_response_service import reprocess_preserved_response


async def _parent(session, run_id, actor_id):
    parent = await get_job(session, run_id, actor_id)
    if parent.target_type != workflow.TARGET_RUN:
        raise ConflictError("不是当前内容规划任务")
    return await workflow._lock_parent(session, parent.id)


async def preview(session, run_id: int, actor_id: int) -> dict:
    parent = await _parent(session, run_id, actor_id)
    await workflow.validate_context(session, parent)
    children = await workflow.current_children(session, parent)
    failed = []
    for child in children:
        if child.status not in {"failed", "cancelled"}:
            continue
        receipt = await session.scalar(
            select(JobTextResponse)
            .where(JobTextResponse.job_id == child.id)
            .order_by(JobTextResponse.id.desc())
            .limit(1)
        )
        submission = child.payload.get("text_submission") or {}
        intact = bool(
            receipt
            and receipt.expires_at.replace(tzinfo=None) > utcnow().replace(tzinfo=None)
            and hashlib.sha256(receipt.response_text.encode()).hexdigest()
            == receipt.response_sha256
            and receipt.response_sha256 == submission.get("response_sha256")
            and receipt.call_id == submission.get("call_id")
        )
        failed.append(
            {
                "job_id": child.id,
                "segment_key": child.payload.get("segment_key"),
                "can_process_saved": bool(
                    intact
                    and child.status == "failed"
                    and child.error_code == "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
                ),
                "response_sha256": receipt.response_sha256 if receipt else None,
                "submission_unknown": bool(submission and not submission.get("response_received")),
                "previous_call_may_be_charged": bool(submission),
            }
        )
    active = [child.id for child in children if child.status not in workflow.TERMINAL]
    pending = parent.payload["expected_total"] - len(children)
    analysis_retry = any(
        child.target_type == workflow.TARGET_ANALYSIS and child.status != "succeeded"
        for child in children
    )
    data = {
        "run_id": run_id,
        "epoch": parent.payload["epoch"],
        "plan_id": parent.payload.get("planning_record_id"),
        "active_job_ids": active,
        "failed": failed,
        "pending_count": pending,
        "completed_count": sum(child.status == "succeeded" for child in children),
        "can_finalize_locally": bool(
            parent.status == "failed"
            and children
            and pending == 0
            and all(child.status == "succeeded" for child in children)
        ),
        "new_text_calls_upper_bound": 1 + workflow.MAX_DETAILS
        if analysis_retry
        else len(failed) + pending,
        "model_called": False,
    }
    return {**data, "fingerprint": workflow._digest(data)}


async def process_saved(session, run_id: int, actor_id: int, *, expected_fingerprint: str) -> dict:
    state = await preview(session, run_id, actor_id)
    if state["fingerprint"] != expected_fingerprint:
        raise ConflictError("任务恢复范围已变化，请刷新后确认")
    parent = await _parent(session, run_id, actor_id)
    # This transaction never authorizes expansion of the paid queue, including
    # when a recovered analysis makes a new frozen plan available.
    parent.payload = {**parent.payload, "allow_dispatch": False}
    await session.flush()
    children = {child.id: child for child in await workflow.current_children(session, parent)}
    for item in state["failed"]:
        if item["can_process_saved"]:
            await reprocess_preserved_response(session, children[item["job_id"]], actor_id)
    await workflow.aggregate_run(session, parent)
    return await preview(session, run_id, actor_id)


async def continue_paid(
    session,
    run_id: int,
    actor_id: int,
    *,
    expected_fingerprint: str,
    request_id: str,
    acknowledge_new_charges: bool,
    acknowledge_unknown_submission: bool = False,
) -> dict:
    if not request_id.strip() or len(request_id) > 128 or acknowledge_new_charges is not True:
        raise ValidationError("新增文本调用可能重复计费，请明确确认并提供请求编号")
    parent = await _parent(session, run_id, actor_id)
    requests = parent.payload.get("paid_continuations") or []
    request = {
        "id": request_id,
        "fingerprint": expected_fingerprint,
        "acknowledge_unknown_submission": acknowledge_unknown_submission,
    }
    previous = next((item for item in requests if item["id"] == request_id), None)
    if previous:
        if any(previous.get(key) != value for key, value in request.items()):
            raise ConflictError("恢复请求编号已被用于其他范围")
        return await preview(session, run_id, actor_id)
    state = await preview(session, run_id, actor_id)
    if state["fingerprint"] != expected_fingerprint:
        raise ConflictError("任务恢复范围已变化，请刷新后确认费用与范围")
    if state["active_job_ids"]:
        raise ConflictError("仍有已提交任务，请等待结果后再确认重新调用")
    if not state["new_text_calls_upper_bound"]:
        raise ConflictError("当前规划已经完成，无需重新调用模型")
    if (
        any(item["submission_unknown"] for item in state["failed"])
        and acknowledge_unknown_submission is not True
    ):
        raise ConflictError("此前请求结果未知，可能已经计费；请核对渠道后明确确认重复调用风险")
    if len(requests) >= 256:
        raise ConflictError("本次规划恢复次数过多，请核对渠道和输入后创建新规划")
    children = {child.id: child for child in await workflow.current_children(session, parent)}
    for item in state["failed"]:
        old = children[item["job_id"]]
        # Rebuild from frozen inputs, not a prompt already augmented by a
        # provider or style adapter on the previous attempt.
        if old.target_type == workflow.TARGET_ANALYSIS:
            _, sources, _ = await workflow.validate_context(session, parent)
            bindings = tuple(
                SourceReferenceBinding.model_validate_json(json.dumps(item))
                for item in parent.payload["request_spec"]["reference_bindings"]
            )
            prompt = bound_analysis_prompt(sources, bindings, parent.payload["reference_snapshot"])
        else:
            record = await session.get(
                EpisodePlanningRecord, parent.payload.get("planning_record_id")
            )
            if record is None:
                raise ConflictError("冻结计划不存在，不能重新调用详细脚本")
            from app.services.episode_planning_optimization import prompt_for_detail

            prompt = prompt_for_detail(
                parent, read_plan(record), old.payload["segment_key"], read_content_analysis(record)
            )
        child = await workflow._new_child(
            session, parent, old.target_type, prompt, segment_key=old.payload.get("segment_key")
        )
        child.payload = {
            **child.payload,
            "recovery": {
                "replaces_job_id": old.id,
                "request_id": request_id,
                "actor_id": actor_id,
                "new_charges_acknowledged": True,
            },
        }
        old.payload = {
            **old.payload,
            "resolution": {"status": "replacement_pending", "by_job_id": child.id},
        }
    parent.payload = {
        **parent.payload,
        "allow_dispatch": True,
        "paid_continuations": [
            *requests,
            {
                **request,
                "actor_id": actor_id,
                "new_charges_acknowledged": True,
                "accepted_at": utcnow().isoformat(),
                "new_text_calls_upper_bound": state["new_text_calls_upper_bound"],
            },
        ],
    }
    parent.status, parent.finished_at = "processing", None
    await session.flush()
    await workflow.aggregate_run(session, parent)
    return await preview(session, run_id, actor_id)
