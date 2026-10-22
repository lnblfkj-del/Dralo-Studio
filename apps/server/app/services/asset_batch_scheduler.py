"""Persisted image dependency gates, reconciled on every worker admission tick."""

# ruff: noqa: RUF001

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.core.errors import AppError, ConflictError
from app.models import Asset, Job, ProjectAssetLink, ProviderModel, utcnow
from app.services.asset_consistency_images import (
    freeze_image_references,
    image_context,
    image_dependencies,
    prerequisite_binding,
)

KEY = "asset_batch_dependencies"


async def lock_transition(session, job=None):
    # Match PostgreSQL admission lock before taking Project/Job row locks.
    if session.bind.dialect.name == "postgresql":
        if job is not None and not ((job.payload or {}).get(KEY) or (job.payload or {}).get("asset_image_preparation") or
                                    (job.payload or {}).get("asset_image_batch_contract") == "asset-image-batch.v6"):
            return
        await session.execute(select(func.pg_advisory_xact_lock(76422001)))


async def stage_children(session, parent, children, plan):
    by_asset = {}
    for child in children:
        by_asset.setdefault(child.target_id, []).append(child)
    deps = {item["asset_id"]: item["prerequisite_asset_ids"] for item in plan["dependencies"]}
    explicit = {item["asset_id"]: item.get("explicit_asset_ids", []) for item in plan["dependencies"]}
    plans = {item["asset_id"]: item for item in plan["dependencies"]}
    # Materialize every direction now. expected_total and IDs survive crashes and retries.
    for child in children:
        siblings = by_asset[child.target_id]
        predecessor = siblings[0].id if child is not siblings[0] else None
        prerequisite_jobs = [other.id for ident in deps[child.target_id]
                             for other in by_asset.get(ident, [])]
        if predecessor:
            prerequisite_jobs.append(predecessor)
        child.payload = {**child.payload, KEY: {
            "asset_ids": deps[child.target_id], "job_ids": prerequisite_jobs,
            "primary_job_id": predecessor, "explicit_asset_ids": explicit[child.target_id],
            "base_prompt": child.payload["prompt"],
            "base_reference_media_ids": child.payload.get("reference_media_ids", []),
            "relation_fingerprint": plans[child.target_id].get("relation_fingerprint"),
            "frozen_bindings": plans[child.target_id].get("frozen_bindings", {}),
            "source_fingerprint": plans[child.target_id].get("source_fingerprint"),
            "own_binding": plans[child.target_id].get("own_binding"),
        }, "dependency_state": "waiting"}
        child.status = "waiting"
    await session.flush()


def blocked(job, code, reason):
    job.status = "failed"
    job.error_code, job.error_message = code, reason
    job.finished_at = utcnow()
    job.payload = {**job.payload, "dependency_state": "blocked"}
    job.worker_id = job.lease_expires_at = None


async def prepare_batch(session, parent):
    if parent.status != "processing" or parent.deleted_at is not None or parent.batch_paused_at:
        return
    from app.services.asset_consistency_service import lock_project
    await lock_transition(session)
    await lock_project(session, parent.project_id)
    # Flush the just-finalized result before refreshing the shared child graph.
    await session.flush()
    await session.refresh(parent)
    if parent.status != "processing" or parent.deleted_at is not None or parent.batch_paused_at:
        return
    children = list((await session.scalars(select(Job).where(Job.parent_job_id == parent.id)
                                          .execution_options(populate_existing=True))).all())
    by_id = {child.id: child for child in children}
    if not any(child.status == "waiting" for child in children):
        return
    context = await image_context(session, parent.project_id)
    for child in children:
        gate = (child.payload or {}).get(KEY)
        if not gate or child.status != "waiting":
            continue
        prerequisites = [by_id.get(ident) for ident in gate["job_ids"]]
        if any(item is None or item.deleted_at is not None or item.status in {"failed", "cancelled"}
               for item in prerequisites):
            blocked(child, "ASSET_DEPENDENCY_FAILED", "前置任务失败或已取消，请重试失败的前置任务后继续")
            continue
        if any(item.status != "succeeded" for item in prerequisites):
            continue
        if gate.get("prepared"):
            child.status = "queued"
            child.payload = {**child.payload, "dependency_state": "ready"}
            continue
        try:
            from app.services.asset_batch_prompt_preparation import prepare_prompt
            if not await prepare_prompt(session, child, by_id, context):
                continue
            gate = child.payload[KEY]
            model = await session.get(ProviderModel, child.payload["provider_model_id"])
            if not model:
                raise ConflictError("图片模型不可用")
            from app.services.asset_batch_planning import source_fingerprint
            asset = await session.get(Asset, child.target_id)
            link = await session.scalar(select(ProjectAssetLink).where(
                ProjectAssetLink.project_id == child.project_id, ProjectAssetLink.asset_id == child.target_id,
            ).execution_options(populate_existing=True))
            if not asset or not link or link.production_archived or source_fingerprint(asset, link) != gate["source_fingerprint"]:
                raise ConflictError("资产资料在确认后变化，请重新预览")
            if gate.get("relation_fingerprint"):
                current_plan = await image_dependencies(session, child.project_id, child.target_id,
                    costume_mode=child.payload.get("parameters", {}).get("costume_mode", "worn"), context=context)
                if current_plan["relation_fingerprint"] != gate["relation_fingerprint"]:
                    raise ConflictError("资产关系在确认后变化，请重新预览")
            frozen_bindings = {str(child.target_id): gate.get("own_binding"), **gate.get("frozen_bindings", {})}
            refs = list(gate["base_reference_media_ids"])
            explicit = gate.get("explicit_asset_ids", [])
            for ident in explicit:
                binding = frozen_bindings.get(str(ident)) or await prerequisite_binding(session, child.project_id, ident, context=context)
                refs.append(binding["media_file_id"])
            primary = by_id.get(gate.get("primary_job_id"))
            if primary:
                primary_media = (primary.result or {}).get("media_file_id")
                if not primary_media:
                    raise ConflictError("主视图任务成功但没有可用图片素材")
                refs.insert(0, primary_media)
            if child.payload.get("asset_type") == "reference":
                prompt, refs, snapshot = gate["base_prompt"], list(dict.fromkeys(refs)), None
            else:
                prompt, refs, snapshot = await freeze_image_references(
                    session, child.project_id, child.target_id, model, gate["base_prompt"], refs,
                    parameters=child.payload.get("parameters", {}),
                    costume_mode=child.payload.get("parameters", {}).get("costume_mode", "worn"),
                    context=context,
                    binding_overrides=frozen_bindings,
                )
            roles = list((snapshot or {}).get("references") or [])
            if primary:
                prompt += f"\nReference image {refs.index(primary_media) + 1}: primary view of the SAME asset. Preserve geometry, identity, clothing and materials; change only viewing direction."
                roles.append({"media_file_id": primary_media, "role": "primary_view",
                              "reference_index": refs.index(primary_media) + 1})
            style = child.payload.get("style_snapshot")
            if style and style.get("media_id"):
                prompt += f"\nReference image {refs.index(style['media_id']) + 1}: project rendering style only; preserve asset identity."
                roles.append({"media_file_id": style["media_id"], "role": "style", "purpose": "project_style",
                              "media_hash": style.get("media_hash"), "media_path": style.get("media_path"),
                              "reference_index": refs.index(style["media_id"]) + 1})
            from app.services.image_model_contract import validate_image_inputs
            validate_image_inputs(model, child.payload.get("parameters", {}), refs)
            child.payload = {**child.payload, "prompt": prompt, "reference_media_ids": refs,
                             KEY: {**gate, "prepared": True},
                             "consistency_image_snapshot": snapshot, "image_reference_roles": roles,
                             "dependency_state": "ready"}
            from app.services.asset_consistency_images import validate_frozen_image_references
            await validate_frozen_image_references(session, child)
            child.status = "queued"
            child.error_code = child.error_message = None
            child.finished_at = None
        except AppError as exc:
            blocked(child, "ASSET_DEPENDENCY_UNAVAILABLE", exc.message)
    await session.flush()


async def tick(session):
    pending = aliased(Job)
    parents = list((await session.scalars(select(Job).where(
        Job.target_type == "asset_image_batch", Job.status == "processing", Job.deleted_at.is_(None),
        Job.payload["asset_image_batch_contract"].as_string() == "asset-image-batch.v6",
        select(pending.id).where(pending.parent_job_id == Job.id, pending.status == "waiting").exists(),
    ))).all())
    for parent in parents:
        await prepare_batch(session, parent)
        first = await session.scalar(select(Job.id).where(Job.parent_job_id == parent.id).limit(1))
        if first:
            from app.services.job_state_result_service import aggregate_parent_job
            await aggregate_parent_job(session, first)


async def retry(session, job):
    """Reset only unsuccessful work, preserving successful media and frozen inputs."""
    parent = job if job.target_type == "asset_image_batch" else await session.get(Job, job.parent_job_id)
    from app.services.asset_consistency_service import lock_project
    await lock_transition(session, job)
    await lock_project(session, parent.project_id)
    await session.refresh(parent)
    if parent.deleted_at:
        raise ConflictError("请先从回收站恢复批次")
    children = list((await session.scalars(select(Job).where(Job.parent_job_id == parent.id)
                                          .execution_options(populate_existing=True))).all())
    if job.id != parent.id:
        job = next(child for child in children if child.id == job.id)
    if job.status not in {"failed", "waiting"}:
        raise ConflictError("仅失败或等待中的图片任务可以重试；已取消任务需重新预览确认")
    targets = children if job.id == parent.id else [job]
    local_responses = []
    for child in targets:
        if child.status not in {"failed", "waiting"}:
            continue
        payload = child.payload or {}
        if child.job_type == "text":
            submission = payload.get("text_submission") or {}
            if submission.get("status") == "submitted" and not submission.get("response_received"):
                raise ConflictError("文本请求结果尚未确认，请先在任务中心核对渠道结果")
            if submission.get("response_received"):
                if child.error_code == "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED":
                    local_responses.append(child)
                    continue
                raise ConflictError("文本响应已保存，请在任务中心核对结果")
        submission = payload.get("image_submission", {})
        recoverable = bool(submission.get("id") or (submission.get("attempted") and submission.get("business_id")))
        if not recoverable and (payload.get("media_submission", {}).get("started") or payload.get("image_submission", {}).get("attempted")):
            raise ConflictError("图片提交结果需先核对渠道记录，系统不会自动重复提交")
        if child.status == "failed" and child.retry_block_reason and child.error_code not in {"ASSET_DEPENDENCY_FAILED", "ASSET_DEPENDENCY_UNAVAILABLE"}:
            raise ConflictError(child.retry_block_reason)
    for child in local_responses:
        from app.services.job_text_response_service import reprocess_preserved_response
        await reprocess_preserved_response(session, child, parent.owner_id)
    for child in targets:
        if child in local_responses:
            continue
        if child.status not in {"failed", "waiting"}:
            continue
        submission = (child.payload or {}).get("image_submission", {})
        recoverable = bool(submission.get("id") or (submission.get("attempted") and submission.get("business_id")))
        child.status = "processing" if recoverable else "queued" if child.job_type == "text" else "waiting"
        if recoverable:
            child.execution_phase = "poll"
            child.payload = {**child.payload, "image_submission": {**submission, "attempted": True}}
        child.payload = {**child.payload, "dependency_state": "waiting"}
        child.error_code = child.error_message = child.finished_at = child.started_at = None
        child.worker_id = child.lease_expires_at = child.available_at = None
        child.progress = child.attempts = 0
        child.result = None
    # Recover descendants blocked by a directly retried prerequisite as well.
    if job.id != parent.id:
        for child in children:
            if child.error_code == "ASSET_DEPENDENCY_FAILED":
                child.status = "waiting"
                child.error_code = child.error_message = child.finished_at = None
    parent.status, parent.finished_at = "processing", None
    parent.error_code = parent.error_message = None
    await prepare_batch(session, parent)
    if children:
        from app.services.job_state_result_service import aggregate_parent_job
        await aggregate_parent_job(session, children[0].id)
    return job
