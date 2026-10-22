"""Per-shot storyboard images. Derived assets never rewrite a segment document."""

from copy import deepcopy
from types import SimpleNamespace
import re

from sqlalchemy import select

from app.core.errors import ConflictError, AppError
from app.models import Asset, Job, JOB_TYPE_IMAGE, JOB_STATUS_PROCESSING
from app.services import asset_service, job_service
from app.services.asset_binding_service import resolve_asset_binding
from app.services.segment_first_frame_service import (
    _digest, _opening_frame_description, _prepare_batch, TARGET_SEGMENT_FIRST_FRAME_BATCH,
)
from app.services.pricing_service import estimate
from app.services.job_pricing_service import _aggregate_video_pricing


def shot_specs(segment):
    script = (segment.parameters or {}).get("structured_script") or {}
    document = script.get("editor_document")
    groups, prefix = [], []
    if isinstance(document, dict):
        for paragraph in document.get("content", []):
            marker = next((n.get("attrs", {}) for n in paragraph.get("content", [])
                           if n.get("type") == "scriptTool" and n.get("attrs", {}).get("kind") == "shot"), None)
            if marker is not None:
                groups.append({"duration": marker.get("duration"), "paragraphs": []})
            (groups[-1]["paragraphs"] if groups else prefix).append(paragraph)
    else:
        groups = [{"camera": camera, "duration": camera.get("duration")} for camera in script.get("camera", [])]
    if not groups:
        raise ConflictError("正文缺少可识别的镜头边界，请先保存镜头脚本")
    specs = []
    scene_context = []
    for index, group in enumerate(groups, 1):
        scoped = deepcopy(script)
        if document:
            def paragraph_text(paragraph):
                return "".join(n.get("text", "") for n in paragraph.get("content", []))
            scene_lines = [p for p in group["paragraphs"] if paragraph_text(p).startswith("场景")]
            if scene_lines:
                scene_context = scene_lines
            context = prefix if index == 1 else [p for p in prefix if not re.search(
                r"\[asset-\d+\]\s*(位置|姿态|服装|持有者|在场状态)[：:]", paragraph_text(p))]
            scoped["editor_document"] = {"type": "doc", "content": deepcopy(
                context + ([] if scene_lines else scene_context) + group["paragraphs"])}
        else:
            scoped["camera"] = [group["camera"]]
            if index > 1:
                scoped.pop("entry_state", None)
        proxy = SimpleNamespace(parameters={"structured_script": scoped}, prompt=segment.prompt)
        description = _opening_frame_description(proxy)
        # Include the full source so changed ordering or continuity never reuses an old frame silently.
        fingerprint = _digest({"script": script, "prompt": segment.prompt, "refs": {key: (segment.refs or {}).get(key, []) for key in ("asset_references", "asset_bindings", "reference_media_ids")}, "index": index})
        specs.append({"shot_index": index, "duration": group.get("duration"),
                      "source_fingerprint": fingerprint, "description": description})
    return specs


def frame_slug(segment, spec):
    return f"seg-{segment.lineage_key[:24]}-shot-{spec['shot_index']}-{spec['source_fingerprint'][:20]}"


async def available_frames(session, project, segment):
    try:
        specs = shot_specs(segment)
    except ConflictError:
        return []
    slugs = [frame_slug(segment, spec) for spec in specs]
    assets = {a.slug: a for a in (await session.scalars(select(Asset).where(
        Asset.project_id == project.id, Asset.slug.in_(slugs)
    ))).all()}
    result = []
    for spec in specs:
        row = {k: v for k, v in spec.items() if k != "description"}
        asset = assets.get(frame_slug(segment, spec))
        if asset and (asset.attributes or {}).get("derived_type") == "segment_shot_frame":
            try:
                row.update(await resolve_asset_binding(session, project, asset_id=asset.id, role="reference_image"))
            except AppError:
                pass
        result.append(row)
    return result


async def prepare(session, owner_id, *, shot_indices=None, missing_only=True, **kwargs):
    prepared = await _prepare_batch(session, owner_id, **kwargs)
    project, episode, plan, model, provider, segments, references, _, effective = prepared
    if shot_indices and len(segments) != 1:
        raise ConflictError("单镜头重做只能选择一个片段")
    tasks = []
    for segment in segments:
        specs = shot_specs(segment)
        if shot_indices and not set(shot_indices).issubset({s["shot_index"] for s in specs}):
            raise ConflictError("镜头已变化，请重新打开预检")
        ready = {row["shot_index"] for row in await available_frames(session, project, segment) if row.get("media_file_id")}
        from app.services.segment_asset_references import material_guidance, resolve_materials
        resolved = await resolve_materials(session, project, segment.refs, segment=segment)
        for spec in specs:
            if shot_indices and spec["shot_index"] not in shot_indices:
                continue
            if missing_only and spec["shot_index"] in ready:
                continue
            # The shared material set anchors identities across every shot; each prompt has its own staging.
            prompt = ("生成一个镜头的独立分镜定格图，不是拼图，不含文字。保持参考角色、服装、场景、道具外观一致。"
                      "仅依据该镜头呈现构图、人物站位及空间关系，不把其他镜头的动作放入画面。\n"
                      f"片段：{segment.title}；镜头 {spec['shot_index']}\n{spec['description']}\n"
                      + material_guidance({**resolved, "reference_media_ids": references[segment.id]}))
            tasks.append({"segment": segment, "spec": spec, "prompt": prompt, "references": references[segment.id]})
    if not tasks:
        raise ConflictError("没有缺失的镜头图；可单独选择镜头重新生成")
    if len(tasks) > 200:
        raise ConflictError("单次最多生成200张镜头图，请缩小所选片段范围")
    return prepared, tasks


async def plan_batch(session, owner_id, *, negative_prompt=None, shot_indices=None, missing_only=True, **kwargs):
    prepared, tasks = await prepare(session, owner_id, shot_indices=shot_indices, missing_only=missing_only, **kwargs)
    project, episode, plan, model, provider, segments, _, _, effective = prepared
    pricing = _aggregate_video_pricing([estimate(model, task["prompt"], effective) for task in tasks])
    pricing["reason"] = "按镜头图片数量估算，实际费用以渠道账单为准"
    return {"project_id": project.id, "episode_id": episode.id, "provider_model_id": model.id,
            "provider_name": provider.name, "model_id": model.model_id, "plan_id": plan.id,
            "plan_revision": plan.revision, "segment_ids": [s.id for s in segments],
            "estimated_count": len(tasks), "parameters": effective, "negative_prompt": negative_prompt,
            "frame_scope": "shots", "shot_indices": shot_indices, "missing_only": missing_only,
            "source_fingerprint": _digest([(t["segment"].id, t["spec"], t["references"]) for t in tasks]),
            "pricing_estimate": pricing, "requires_confirmation": True}


async def create_batch(session, owner_id, *, request_id, max_cost_cents, expected_plan_id,
                       expected_plan_revision, source_fingerprint=None, negative_prompt=None,
                       shot_indices=None, missing_only=True, **kwargs):
    prepared, tasks = await prepare(session, owner_id, shot_indices=shot_indices, missing_only=missing_only, **kwargs)
    project, episode, plan, model, _, segments, _, _, effective = prepared
    preview = await plan_batch(session, owner_id, shot_indices=shot_indices, missing_only=missing_only,
                               negative_prompt=negative_prompt, **kwargs)
    if plan.id != expected_plan_id or plan.revision != expected_plan_revision or source_fingerprint != preview["source_fingerprint"]:
        raise ConflictError("镜头或参考素材已变化，请重新预检")
    cents = preview["pricing_estimate"].get("estimated_cents")
    if type(cents) is not int or cents > max_cost_cents:
        raise ConflictError("镜头图费用未知或已变化，请重新确认")
    fingerprint = _digest({"source": source_fingerprint, "model": model.id, "parameters": effective,
                           "negative_prompt": negative_prompt})
    prior = (await session.scalars(select(Job).where(Job.project_id == project.id,
             Job.owner_id == owner_id, Job.target_type == TARGET_SEGMENT_FIRST_FRAME_BATCH))).all()
    for job in prior:
        if (job.payload or {}).get("request_id") == request_id:
            if job.payload.get("request_fingerprint") != fingerprint:
                raise ConflictError("请求编号已用于其他镜头图任务")
            return job
    parent = Job(owner_id=owner_id, project_id=project.id, job_type=JOB_TYPE_IMAGE,
                 status=JOB_STATUS_PROCESSING, progress=0, target_type=TARGET_SEGMENT_FIRST_FRAME_BATCH,
                 target_id=episode.id, payload={"request_id": request_id, "request_fingerprint": fingerprint,
                 "frame_scope": "shots", "provider_model_id": model.id, "plan_id": plan.id,
                 "segment_ids": [s.id for s in segments], "parameters": effective},
                 result={"total": len(tasks), "completed": 0, "succeeded": 0, "failed": 0,
                         "cancelled": 0, "child_job_ids": [], "children": []})
    session.add(parent)
    await session.flush()
    children = []
    for task in tasks:
        segment, spec = task["segment"], task["spec"]
        slug = frame_slug(segment, spec)
        asset = await session.scalar(select(Asset).where(Asset.project_id == project.id, Asset.slug == slug))
        if asset is None:
            asset = await asset_service.create_asset(session, project, {
                "asset_type": "reference", "name": f"第{episode.number}集片段{segment.order:02d}镜头{spec['shot_index']:02d}",
                "slug": slug, "prompt_anchor": task["prompt"], "attributes": {
                    "derived_type": "segment_shot_frame", "episode_id": episode.id,
                    "segment_lineage_key": segment.lineage_key, "shot_index": spec["shot_index"],
                    "source_fingerprint": spec["source_fingerprint"]}})
        elif (asset.attributes or {}).get("derived_type") != "segment_shot_frame":
            raise ConflictError("镜头图资产名称冲突")
        child = await job_service.create_image_job(session, owner_id, project_id=project.id,
            asset_id=asset.id, provider_model_id=model.id, prompt=task["prompt"],
            negative_prompt=negative_prompt, reference_media_ids=task["references"], parameters=effective,
            view_type="base", view_label=f"镜头 {spec['shot_index']:02d}")
        child.parent_job_id = parent.id
        # No segment_frame callback: completion cannot mutate scripts, refs or the active editor.
        children.append(child)
    parent.cost_estimate = cents
    parent.payload = {**parent.payload, "pricing_snapshot": preview["pricing_estimate"]}
    parent.result = {**parent.result, "child_job_ids": [c.id for c in children], "estimated_cost": preview["pricing_estimate"]}
    await session.flush()
    return parent
