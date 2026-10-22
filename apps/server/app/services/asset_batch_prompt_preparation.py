"""Durable prompt preparation within a confirmed image batch."""

# ruff: noqa: RUF001

from app.core.errors import ConflictError
from app.services.asset_batch_planning import digest, expand_references


def stable_source(asset, link):
    return digest({"description": asset.description, "attributes": asset.attributes,
                   "data": {k: v for k, v in (link.production_data or {}).items() if k not in {
                       "prompt_anchor", "prompt_optimization", "consistency_design", "consistency_design_job_id",
                       "adoptions", "consistency_image_basis"}}})


async def stage_prompts(session, project, parent, images, plan):
    from app.services.asset_consistency_images import image_context
    from app.services.asset_prompt_service import create_proposal_job
    context = await image_context(session, project.id)
    jobs = []
    ids = plan["prompt_asset_ids"]
    for offset in range(0, len(ids), 8):
        job = await create_proposal_job(session, project, asset_ids=ids[offset:offset + 8],
            provider_model_id=plan["text_model_id"], request_id=f"image-batch:{parent.id}:prompts:{offset}", parameters={})
        job.parent_job_id = parent.id
        job.payload = {**job.payload, "asset_image_preparation": True}
        jobs.append(job)
    for child in images:
        row = context["by_id"].get(child.target_id)
        if not row:
            continue
        needed = {child.target_id, *child.payload["asset_batch_dependencies"]["asset_ids"]}
        relevant = [job for job in jobs if needed.intersection(job.payload["asset_ids"])]
        if not relevant:
            continue
        child.payload = {**child.payload, "automatic_prompt": {
            "job_ids": [job.id for job in relevant],
            "own_job_id": next((job.id for job in jobs if child.target_id in job.payload["asset_ids"]), None),
            "stable_source": stable_source(row["asset"], row["link"]),
            "original_prompt": row["prompt"],
        }}
    parent.payload = {**parent.payload, "expected_total": len(images) + len(jobs),
                      "prompt_job_ids": [job.id for job in jobs]}
    await session.flush()


async def prepare_prompt(session, child, by_id, context):
    """Wait for saved responses; never overwrite a user's prompt or broaden refs."""
    state = child.payload.get("automatic_prompt")
    if not state or state.get("prepared"):
        return True
    jobs = [by_id[ident] for ident in state["job_ids"]]
    if any(job.status not in {"succeeded", "failed", "cancelled"} for job in jobs):
        return False
    row = context["by_id"].get(child.target_id)
    if not row or stable_source(row["asset"], row["link"]) != state["stable_source"]:
        raise ConflictError("资产资料已修改，请重新生成")
    own = by_id.get(state["own_job_id"])
    if own:
        application = (own.result or {}).get("prompt_application") or {}
        if own.status != "succeeded" or child.target_id not in [*application.get("applied", []), *application.get("unchanged", [])]:
            raise ConflictError("提示词未生成成功，请重试失败项")
        from app.services.asset_prompt_state import asset_snapshot, input_hash
        marker = (row["link"].production_data or {}).get("prompt_optimization") or {}
        if marker.get("job_id") != own.id or marker.get("applied_hash") != input_hash(asset_snapshot(row["asset"], row["link"])):
            raise ConflictError("提示词已被修改，请重新生成")
    elif row["prompt"] != state["original_prompt"]:
        raise ConflictError("提示词已被修改，请重新生成")
    gate = child.payload["asset_batch_dependencies"]
    base = gate["base_prompt"]
    if own:
        rows = {ident: (item["asset"], item["link"]) for ident, item in context["by_id"].items()}
        prompt, refs = expand_references(rows, row["prompt"])
        if set(refs) - {child.target_id, *gate["asset_ids"]}:
            raise ConflictError("生成的提示词新增了参考资产，请重新确认生成范围")
        base = base.replace(f"ASSET_PROMPT_PENDING_{child.target_id}", prompt)
    from app.services.asset_batch_planning import source_fingerprint
    from app.services.asset_consistency_images import image_dependencies
    relation = await image_dependencies(session, child.project_id, child.target_id, context=context,
        costume_mode=child.payload.get("parameters", {}).get("costume_mode", "worn"))
    child.payload = {**child.payload,
        "automatic_prompt": {**state, "prepared": True},
        "asset_batch_dependencies": {**gate, "base_prompt": base,
            "source_fingerprint": source_fingerprint(row["asset"], row["link"]),
            "relation_fingerprint": relation["relation_fingerprint"]}}
    return True
