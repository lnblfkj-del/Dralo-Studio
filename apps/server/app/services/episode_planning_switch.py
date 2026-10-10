"""Persist a local model replan; only explicit continuation may queue new calls."""

import json
from copy import deepcopy
from dataclasses import replace

from sqlalchemy import func, select

from app.core.errors import ConflictError, ValidationError
from app.models import Job, utcnow
from app.models.episode_planning import EpisodePlanningRecord
from app.schemas.episode_planning import FrozenEpisodePlan, SegmentDetail
from app.schemas.episode_references import SourceReferenceBinding
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_capability import load_capability
from app.services.episode_planning_contract import fingerprint
from app.services.episode_planning_coordinator import replan_for_model, reusable_details
from app.services.episode_planning_references import resolve_bindings
from app.services.episode_planning_solver import PlanningTimeoutError
from app.services.episode_planning_storage import new_plan_record, read_content_analysis, read_plan


async def switch_model(
    session,
    parent,
    actor_id,
    *,
    request_id,
    expected_plan_fingerprint,
    expected_capability_fingerprint,
    video_model_id,
    mode_key,
):
    parent = await workflow._lock_parent(session, parent.id)
    operation = {
        "request_id": request_id,
        "source_run_id": parent.id,
        "expected_plan_fingerprint": expected_plan_fingerprint,
        "expected_capability_fingerprint": expected_capability_fingerprint,
        "video_model_id": video_model_id,
        "mode_key": mode_key,
    }
    # A committed switch makes the original run stale. Recover the same response
    # before validating freshness, but never reuse an id for a different choice.
    runs = (
        await session.scalars(
            select(Job).where(
                Job.target_type == workflow.TARGET_RUN,
                Job.target_id == parent.target_id,
                Job.project_id == parent.project_id,
            )
        )
    ).all()
    for run in runs:
        if run.payload.get("request_id") == request_id:
            if run.payload.get("model_switch") != operation or run.deleted_at is not None:
                raise ConflictError("请求编号已经用于其他规划操作")
            return run
    episode, sources, _ = await workflow.validate_context(
        session, parent, check_current_capability=False
    )
    children = await workflow.current_children(session, parent)
    if any(child.status not in workflow.TERMINAL for child in children):
        raise ConflictError("仍有片段任务执行中，请等待完成或取消后再切换模型")
    record_id = parent.payload.get("planning_record_id")
    record = await session.get(EpisodePlanningRecord, record_id) if record_id else None
    if (
        record is None
        or record.episode_id != episode.id
        or record.workspace_id != episode.workspace_id
        or record.execution_epoch != parent.payload["epoch"]
    ):
        raise ConflictError("当前任务没有可重规划的冻结计划")
    original = read_plan(record)
    if fingerprint(original) != expected_plan_fingerprint:
        raise ConflictError("片段计划已变化，请重新检查模型兼容性")
    target = await load_capability(session, video_model_id)
    if fingerprint(target) != expected_capability_fingerprint:
        raise ConflictError("目标模型规格已变化，请重新检查兼容性")
    mode = next((item for item in target.modes if item.key == mode_key), None)
    if mode is None:
        raise ValidationError("目标模型模式不存在")
    bindings = tuple(
        SourceReferenceBinding.model_validate_json(json.dumps(item))
        for item in parent.payload["request_spec"]["reference_bindings"]
    )
    resolved = await resolve_bindings(session, episode, sources, mode, bindings)
    if resolved != parent.payload["reference_snapshot"]:
        raise ConflictError("参考媒体已变化，不能沿用先前的分析结果")
    analysis = read_content_analysis(record)
    try:
        result = replan_for_model(original, sources, analysis, target, mode_key)
        if result.plan == original:
            raise ConflictError("已经使用该模型和模式，无需重新规划")
        if len(result.plan.segments) > workflow.MAX_DETAILS:
            raise ValueError("重新规划后的片段数量超过上限")
        revision = (
            await session.scalar(
                select(func.max(EpisodePlanningRecord.revision)).where(
                    EpisodePlanningRecord.episode_id == episode.id,
                    EpisodePlanningRecord.execution_epoch == parent.payload["epoch"],
                )
            )
            or 0
        ) + 1
        raw = result.plan.model_dump(mode="json")
        raw["plan_revision"] = revision
        result = replace(result, plan=FrozenEpisodePlan.model_validate_json(json.dumps(raw)))
        completed = {
            child.payload["segment_key"]: child
            for child in children
            if child.target_type == workflow.TARGET_DETAIL and child.status == "succeeded"
        }
        retained = reusable_details(
            original,
            result,
            tuple(
                SegmentDetail.model_validate_json(json.dumps(child.result["detail"]))
                for child in completed.values()
            ),
        )
        new_record = new_plan_record(
            result.plan, workspace_id=episode.workspace_id, analysis=analysis
        )
    except PlanningTimeoutError as exc:
        raise ConflictError("本地重规划超时，原计划未改变；可重试，不调用模型") from exc
    except ValueError as exc:
        raise ValidationError(f"新模型无法安全承接当前内容：{str(exc)[:500]}") from exc
    session.add(new_record)
    await session.flush()
    run = Job(
        owner_id=parent.owner_id,
        requested_by=actor_id,
        project_id=parent.project_id,
        workspace_id=parent.workspace_id,
        target_type=workflow.TARGET_RUN,
        target_id=parent.target_id,
        job_type="text_batch",
        status="processing",
        max_attempts=1,
        started_at=utcnow(),
        provider_id=parent.provider_id,
        provider=parent.provider,
        model=parent.model,
        execution_policy_snapshot=deepcopy(parent.execution_policy_snapshot),
        payload={
            "request_id": request_id,
            "request_spec": {
                **parent.payload["request_spec"],
                "video_model_id": video_model_id,
                "mode_key": mode_key,
            },
            "epoch": parent.payload["epoch"],
            "sources": deepcopy(parent.payload["sources"]),
            "asset_fingerprint": parent.payload["asset_fingerprint"],
            "reference_snapshot": resolved,
            "capability": target.model_dump(mode="json"),
            "capability_fingerprint": fingerprint(target),
            "editorial_target_ms": parent.payload["editorial_target_ms"],
            "expected_total": 1 + len(result.plan.segments),
            "allow_dispatch": False,
            "planning_record_id": new_record.id,
            "model_switch": operation,
            "switch_authorization": {
                "actor_id": actor_id,
                "confirmed_at": utcnow().isoformat(),
                "model_called": False,
            },
            "replaced_segment_keys": list(result.replaced_segment_keys),
        },
    )
    session.add(run)
    await session.flush()
    analysis_child = next(
        (
            child
            for child in children
            if child.target_type == workflow.TARGET_ANALYSIS and child.status == "succeeded"
        ),
        None,
    )
    if analysis_child is None:
        raise ConflictError("缺少成功的原始分析来源，不能创建重规划版本")

    def reuse(source, target_type, result_value, segment_key=None):
        # Explicit zero-call audit records, never queued or passed to a provider.
        session.add(
            Job(
                owner_id=parent.owner_id,
                requested_by=actor_id,
                workspace_id=parent.workspace_id,
                project_id=parent.project_id,
                parent_job_id=run.id,
                target_id=run.target_id,
                target_type=target_type,
                job_type="text",
                status="succeeded",
                progress=100,
                attempts=0,
                max_attempts=1,
                cost_estimate=0,
                finished_at=utcnow(),
                payload={
                    "planning_run_id": run.id,
                    "planning_epoch": run.payload["epoch"],
                    "segment_key": segment_key,
                    "reused_from_job_id": source.id,
                    "source_plan_fingerprint": fingerprint(original),
                    "model_called": False,
                },
                result={**result_value, "planning_record_id": new_record.id, "model_called": False},
            )
        )

    reuse(analysis_child, workflow.TARGET_ANALYSIS, {"stage": "content_frozen"})
    for detail in retained:
        reuse(
            completed[detail.segment_key],
            workflow.TARGET_DETAIL,
            {"stage": "detail_saved", "detail": detail.model_dump(mode="json")},
            detail.segment_key,
        )
    await session.flush()
    await workflow.aggregate_run(session, run)
    return run
