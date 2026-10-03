"""Durable, serial C6-A orchestration. No provider I/O happens in this executor.

One transaction checkpoints one step and its child job. SQLite's project write lock
serializes controls, confirmation and executor processes. Failed/unknown child jobs
are never resubmitted here; their existing recovery protocol remains authoritative.
"""

# ruff: noqa: RUF001
from copy import deepcopy
from app.services.pricing_service import estimate

from sqlalchemy import select

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Job, MediaFile, Project, User, utcnow
from app.schemas.canvas import CanvasSave
from app.schemas.canvas_workflow import GraphOperation, WorkflowPlan
from app.services import canvas_generation_service as generation
from app.services import canvas_service, job_service
from app.providers.protocols import model_confirmation_token


def event(run, action, actor):
    run["version"] += 1
    run["events"] = [
        *run.get("events", []),
        {"action": action, "actor": actor, "at": utcnow().isoformat(), "step": run["cursor"]},
    ][-128:]


async def persist(session, job, preview):
    from app.services.canvas_agent_service import _sync_action_message

    job.result = {**(job.result or {}), "action_preview": preview}
    public = deepcopy(preview)
    if public.get("workflow"):
        public["workflow"].pop("undo_snapshot", None)
    await _sync_action_message(session, job, public)
    await session.flush()


async def make_preview(session, plan, source, job):
    estimates, tokens = [], {}
    for step in plan.steps:
        c = step.command
        if c.operation != "generate":
            continue
        model = await generation.validate_model(session, c.provider_model_id, c.kind, c.parameters)
        tokens[str(model.id)] = await model_confirmation_token(session, model)
        quote = estimate(model, c.content, c.parameters)
        estimates.append(
            {
                "step_id": step.id,
                "node_id": c.node_id,
                "model": model.model_id,
                "parameters": c.parameters,
                "estimated_cents": quote["estimated_cents"],
                "pricing_estimate": quote,
            }
        )
    return {
        "kind": "canvas_action",
        "target_type": "canvas_workflow",
        "status": "pending",
        "title": "多步骤画布计划",
        "summary": "按顺序执行；失败保留已完成结果。生成结果绑定需再次确认；取消不保证供应商停止或退款。",
        "source": {**source, "models": tokens},
        "proposed": plan.model_dump(),
        "media_estimates": estimates,
        "trace": {"job_id": job.id, "model": job.model, "agent_workspace": "canvas"},
    }


async def locked_job(session, project, job_id, actor_id):
    await generation.submission_lock(session, project)
    job = await job_service.get_job(session, job_id, actor_id)
    await session.refresh(job)
    if (
        job.project_id != project.id
        or job.target_type != "canvas_agent"
        or job.status != "succeeded"
    ):
        raise NotFoundError("画布计划不存在")
    preview = deepcopy((job.result or {}).get("action_preview", {}))
    if preview.get("target_type") != "canvas_workflow":
        raise ConflictError("不是多步骤画布计划")
    return job, preview


async def start(session, project, job_id, actor_id):
    job, preview = await locked_job(session, project, job_id, actor_id)
    snapshot = await canvas_service.get_snapshot(session, project)
    if preview.get("workflow"):
        return snapshot  # repeated confirmation cannot restart an existing run
    if preview["status"] != "pending":
        raise ConflictError("计划已拒绝，不能执行")
    if snapshot["revision"] != preview["source"].get("revision"):
        raise ConflictError("画布已变化，请重新生成并确认计划")
    plan = WorkflowPlan.model_validate(preview["proposed"])
    if any(e["estimated_cents"] is None and e.get("pricing_estimate", {}).get("amount") is None for e in preview["media_estimates"]):
        raise ConflictError("生成费用未知，未启动。请先核实模型定价后重新生成计划")
    # Scope comes from the authenticated message's server snapshot, never model output.
    allowed = set(preview["source"].get("allowed_nodes", []))
    for step in plan.steps:
        c = step.command
        if c.operation in {"create", "group"}:
            if c.node_id in allowed or any(n["id"] == c.node_id for n in snapshot["nodes"]):
                raise ConflictError("新节点编号已存在")
            if c.operation == "group" and not set(c.node_ids) <= allowed:
                raise ConflictError("组合超出确认范围")
            allowed.add(c.node_id)
        else:
            targets = (
                set(c.node_ids)
                if isinstance(c, GraphOperation) and c.operation == "arrange"
                else {c.node_id}
            )
            if c.operation == "connect":
                targets.add(c.target_id)
            if not targets <= allowed:
                raise ConflictError("步骤超出本次画布/选中节点确认范围")
    reversible = all(
        isinstance(s.command, GraphOperation)
        or s.command.operation == "connect"
        or (s.command.operation in {"create", "update"} and s.command.kind == "text")
        for s in plan.steps
    )
    preview["status"] = "applied"
    preview["workflow"] = {
        "status": "running",
        "cursor": 0,
        "version": 0,
        "revision": snapshot["revision"],
        "allowed_nodes": sorted(allowed),
        "events": [],
        "steps": [{"id": s.id, "status": "pending"} for s in plan.steps],
        "entities": preview["source"].get("entities", {}),
        "undo_snapshot": {"nodes": snapshot["nodes"], "edges": snapshot["edges"]}
        if reversible
        else None,
        "can_undo": False,
    }
    event(preview["workflow"], "confirmed", actor_id)
    await persist(session, job, preview)
    return snapshot


async def control(session, project, job_id, actor_id, payload):
    job, preview = await locked_job(session, project, job_id, actor_id)
    run = preview.get("workflow")
    if not run or run["version"] != payload.expected_version:
        raise ConflictError("执行状态已变化，请刷新后操作")
    action, state = payload.action, run["status"]
    if action == "pause" and state in {"running", "waiting", "review"}:
        run["resume_status"], run["status"] = state, "paused"
    elif action == "resume" and state == "paused":
        run["status"] = run.pop("resume_status", "running")
    elif action == "cancel" and state in {"running", "waiting", "review", "paused", "failed"}:
        run["status"] = "cancelled"
        for step in run["steps"]:
            child = await session.get(Job, step["job_id"]) if step.get("job_id") else None
            if (
                child
                and child.owner_id == actor_id
                and child.project_id == project.id
                and child.status not in job_service.TERMINAL_STATUSES
            ):
                await job_service.cancel_job(session, child)
        run["cancel_scope"] = "local_only"
    elif action == "retry" and state == "failed":
        # Only recheck a failed local step or query the SAME child. Never create another generation.
        run["status"] = "running"
        run.pop("error", None)
    elif action == "approve_binding" and state == "review":
        run["steps"][run["cursor"]]["binding_approved"] = True
        run["status"] = "running"
    elif action == "undo" and state in {"succeeded", "cancelled", "failed"} and run.get("can_undo"):
        old = run["undo_snapshot"]
        snapshot = await canvas_service.get_snapshot(session, project)
        if snapshot["revision"] != run["revision"]:
            raise ConflictError("画布已有后续修改，不能撤销本计划")
        await canvas_service.save_snapshot(
            session,
            project,
            CanvasSave(
                expected_revision=run["revision"],
                viewport=snapshot["viewport"],
                nodes=old["nodes"],
                edges=old["edges"],
            ),
        )
        run["status"], run["can_undo"] = "undone", False
    else:
        raise ConflictError("当前状态不支持此操作")
    event(run, action, actor_id)
    await persist(session, job, preview)
    return await canvas_service.get_snapshot(session, project)


async def tick(session, project, job_id):
    job, preview = await locked_job(session, project, job_id, project.owner_id)
    run = preview.get("workflow")
    if not run or run["status"] not in {"running", "waiting"}:
        return
    plan = WorkflowPlan.model_validate(preview["proposed"])
    record = run["steps"][run["cursor"]]
    checkpoint = deepcopy(run)
    try:
        # Savepoint prevents partially applied tools/jobs when validation fails.
        async with session.begin_nested():
            user = await session.get(User, project.owner_id)
            if not user or not user.is_active:
                raise ConflictError("项目所有者已停用，执行停止")
            from app.services.provider_service import get_ai_settings

            settings = await get_ai_settings(session)
            if not settings.canvas_agent_enabled:
                raise ConflictError("画布 Agent 已停用，后续步骤停止")
            if record.get("job_id"):
                child = await session.get(Job, record["job_id"])
                if (
                    not child
                    or child.project_id != project.id
                    or child.owner_id != project.owner_id
                ):
                    raise NotFoundError("原生成任务不存在，禁止重新提交")
                if child.status in {"failed", "cancelled"}:
                    raise ConflictError(
                        "原生成任务失败或已取消；请在任务中心处理原任务，本计划不会重复提交"
                    )
                if child.status != "succeeded":
                    run["status"] = "waiting"
                    await persist(session, job, preview)
                    return
                media_id = (child.result or {}).get("media_file_id")
                media = await session.get(MediaFile, media_id) if type(media_id) is int else None
                if (
                    not media
                    or media.owner_id != project.owner_id
                    or media.project_id != project.id
                ):
                    raise ConflictError("生成任务尚无可验证的项目内媒体产物")
                record["media_id"] = media.id
            else:
                await execute_step(
                    session, project, job, preview, plan.steps[run["cursor"]], record
                )
                if run["status"] in {"waiting", "review"}:
                    await persist(session, job, preview)
                    return
            record["status"] = "succeeded"
            run["cursor"] += 1
            run["status"] = "succeeded" if run["cursor"] == len(plan.steps) else "running"
            run["can_undo"] = bool(run.get("undo_snapshot"))
            event(run, "step_completed", "executor")
    except Exception as error:
        # A tool implementation error is also a stop, never an automatic retry loop.
        # The savepoint above has already rolled back its writes.
        if not isinstance(error, (AppError, ValueError, TypeError)):
            import logging

            logging.getLogger(__name__).exception("Canvas workflow tool failed")
        run = preview["workflow"] = checkpoint
        record = run["steps"][run["cursor"]]
        record["status"], run["status"] = "failed", "failed"
        run["error"] = (
            error.message
            if isinstance(error, AppError)
            else "工具执行异常，本步骤未提交；请检查后再处理，后续步骤已停止"
        )
        event(run, "step_failed", "executor")
    await persist(session, job, preview)


async def execute_step(session, project, job, preview, step, record):
    from app.services import canvas_graph_tools, canvas_production_service

    run, source = preview["workflow"], preview["source"]
    snapshot = await canvas_service.get_snapshot(session, project)
    if snapshot["revision"] != run["revision"]:
        raise ConflictError("画布已被修改，停止后续步骤；请检查已完成结果并重新生成计划")
    command = step.command.model_copy(deep=True)
    if command.operation in {"update", "bind_media"}:
        target = next((n for n in snapshot["nodes"] if n["id"] == command.node_id), None)
        asset_id = target["data"].get("production_asset_id") if target else None
        if asset_id and any(
            n["data"].get("production_asset_id") == asset_id and n["id"] not in run["allowed_nodes"]
            for n in snapshot["nodes"]
        ):
            raise ConflictError("该角色/场景还被未选中的节点共享，请将相关节点加入范围后重新确认")
    outputs = {s["id"]: s.get("media_id") for s in run["steps"] if s["status"] == "succeeded"}
    if step.media_from_step:
        command.media_id = outputs[step.media_from_step]
        if not record.get("binding_approved"):
            record.update(status="review", media_id=command.media_id)
            run["status"] = "review"
            event(run, "binding_review_required", "executor")
            return
    if step.references_from:
        command.parameters["references"] = [
            *command.parameters.get("references", []),
            *[{"media_id": outputs[r.step_id], "role": r.role} for r in step.references_from],
        ]
    if isinstance(command, GraphOperation):
        snapshot = await canvas_graph_tools.apply(
            session, project, snapshot, command, set(run["allowed_nodes"])
        )
    else:
        if command.operation == "generate":
            estimate = next(e for e in preview["media_estimates"] if e["step_id"] == step.id)
            quote = estimate.get("pricing_estimate")
            if quote and quote.get("amount") is not None:
                command.parameters["max_cost"] = {"amount": quote["amount"], "currency": quote["currency"]}
            else:
                command.parameters["max_cost_cents"] = estimate["estimated_cents"]
        request_id = f"workflow-{job.id}-{step.id}"
        snapshot = await canvas_production_service.apply_plan(
            session,
            project,
            {
                "revision": run["revision"],
                "entities": run["entities"],
                "models": source.get("models", {}),
                "agent_thread_id": job.target_id,
                "request_id": request_id,
            },
            {"type": "canvas_actions", "operations": [command.model_dump()]},
        )
        if command.operation == "generate":
            child = await session.scalar(
                select(Job).where(
                    Job.project_id == project.id,
                    Job.owner_id == project.owner_id,
                    Job.payload["parameters"]["canvas_request_id"].as_string() == request_id,
                )
            )
            if not child:
                raise ConflictError("未找到生成任务记录")
            child.parent_job_id = job.id
            record.update(status="waiting", job_id=child.id)
            run["status"] = "waiting"
            event(run, "generation_submitted", "executor")
    run["revision"] = snapshot["revision"]
    run["entities"] = {
        n["id"]: n["data"].get("production_profile", {}).get("token")
        for n in snapshot["nodes"]
        if n["data"].get("production_asset_id")
    }


async def advance_active():
    """Called by a dedicated supervisor; never claims unrelated queue jobs."""
    import logging

    from app.core.database import SessionLocal
    from app.core.workspace_context import isolation_enabled, system_scope, workspace_scope
    from app.services.workspace_service import require_membership
    from contextlib import nullcontext

    with system_scope():
        async with SessionLocal() as session:
            ids = (
                await session.execute(
                    select(Job.id, Job.project_id, Job.workspace_id, Job.requested_by, Job.owner_id)
                    .where(
                        Job.target_type == "canvas_agent",
                        Job.status == "succeeded",
                        Job.result["action_preview"]["workflow"]["status"]
                        .as_string()
                        .in_(["running", "waiting"]),
                    )
                    .order_by(Job.id)
                    .limit(100)
                )
            ).all()
    for job_id, project_id, workspace_id, requested_by, owner_id in ids:
        try:
            context = nullcontext()
            if isolation_enabled():
                async with SessionLocal() as session:
                    member = await require_membership(session, workspace_id or "", requested_by or owner_id)
                    if member.role == "viewer":
                        continue
                    context = workspace_scope(member.workspace_id, requested_by or owner_id, member.role)
            with context:
                async with SessionLocal() as session:
                    project = await session.get(Project, project_id)
                    if project:
                        await tick(session, project, job_id)
                        await session.commit()
        except Exception:
            logging.getLogger(__name__).exception("Canvas workflow %s checkpoint failed", job_id)


async def supervise():
    import asyncio
    import logging

    while True:
        try:
            await advance_active()
        except Exception:
            logging.getLogger(__name__).exception("Canvas workflow supervisor unavailable")
        await asyncio.sleep(1)
