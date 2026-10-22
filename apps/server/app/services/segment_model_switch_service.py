"""Atomic current-manuscript model changes; no text or media provider calls."""

from copy import deepcopy

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import EpisodeProduction, EpisodeProductionPlan, Job, Project, VideoSegment
from app.services.asset_production_core import fingerprint
from app.services.episode_planning_capability import load_capability
from app.services.job_video_batch_helpers import _compile_segment_media
from app.services.segment_asset_references import catalog, resolve_materials
from app.services.segment_model_fit import content_needs, explain_no_match, fit, matching_modes
from app.services.segment_plan_core_service import get_active_plan
from app.services.segment_plan_write_service import create_plan


async def preview(session, episode, *, provider_model_id, resolution=None):
    current = await get_active_plan(session, episode)
    if current is None:
        raise ConflictError("本集还没有片段脚本")
    if current["source_script_revision"] != episode.script_revision or episode.finalized_script_revision != episode.script_revision:
        raise ConflictError("正文已变化，请先确认正文并整理当前片段脚本")
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id))
    project = await session.get(Project, episode.project_id)
    ratio = (project.creation_settings or {}).get("aspect_ratio")
    if not ratio or ratio == "default":
        raise ConflictError("请先在项目设置中确定画幅比例")
    capability = await load_capability(session, provider_model_id)
    available = sorted({m.resolution for m in capability.modes if m.aspect_ratio == ratio})
    if not available:
        raise ConflictError(f"新模型不支持项目画幅 {ratio}，原模型和脚本未改变")
    assets = await catalog(session, project.id)
    requirements = []
    from app.services.style_generation_service import project_style_media
    style = await project_style_media(session, project.id, episode.owner_id, "video")
    for segment in current["segments"]:
        try:
            refs = await resolve_materials(session, project, segment["refs"], strict=False, assets=assets)
            effective = {**current["parameters"], **segment.get("parameters", {})}
            first, last, image_ids, bindings = _compile_segment_media(refs, effective)
        except ConflictError as exc:
            requirements.append((segment, {}, content_needs(segment), segment["refs"], exc.message))
            continue
        images = set(image_ids)
        unresolved = {a["asset_id"] for a in refs.get("asset_references", []) if a.get("material_required") and
                      not any(b.get("asset_id") == a["asset_id"] for b in bindings)}
        if style and style not in {first, last}:
            images.add(style)
        counts = {"image": len(images) + len(unresolved), "first_frame": int(bool(first or refs.get("continuity"))),
                  "last_frame": int(bool(last))}
        requirements.append((segment, counts, content_needs(segment), refs, None))
    bgm = (production.settings or {}).get("background_music")

    def evaluate(chosen):
        items, changes, blocked = [], [], []
        endings = {}
        for segment, counts, needs, _, material_issue in requirements:
            if material_issue:
                blocked.append({"segment_id": segment["id"], "order": segment["order"], "reason": material_issue})
                continue
            modes = matching_modes(capability, ratio, chosen, counts, needs, bgm)
            parts, reason = fit(segment, modes) if modes else (
                None, explain_no_match(capability, ratio, chosen, counts, needs, bgm))
            if parts and len(parts) > 1 and (counts["first_frame"] or counts["last_frame"]):
                reason = "需要拆分，但当前设置包含首尾帧；请先编辑帧用途，不能把同一帧复制给多个片段"
            if reason:
                blocked.append({"segment_id": segment["id"], "order": segment["order"], "reason": reason})
                continue
            items.extend(parts)
            endings[segment["lineage_key"]] = parts[-1]["lineage_key"]
            if len(parts) != 1 or parts[0]["generation_duration"] != segment["generation_duration"]:
                changes.append({"segment_id": segment["id"], "order": segment["order"], "before": segment["generation_duration"],
                                "after": [p["generation_duration"] for p in parts]})
        # Repoint downstream continuity to the last part, never the first part.
        for item in items:
            item["refs"] = deepcopy(item["refs"])
            continuity = item["refs"].get("continuity")
            if continuity:
                continuity["source_lineage_key"] = endings.get(continuity["source_lineage_key"], continuity["source_lineage_key"])
        return items, changes, blocked

    evaluated = {r: evaluate(r) for r in available}
    valid = [r for r, (_, _, issues) in evaluated.items() if not issues]
    compatible = [r for r, (_, changes, issues) in evaluated.items() if not issues and not changes]
    chosen = resolution or current["parameters"].get("resolution") or (production.settings or {}).get("resolution")
    if chosen not in available:
        if resolution:
            raise ConflictError("该模型不支持所选分辨率，请重新选择")
        chosen = ""
    items, changes, blocked = evaluated[chosen] if chosen else ([], [], [])
    parameters = {k: deepcopy(v) for k, v in current["parameters"].items() if k not in {
        "planning_run_id", "planning_record_id", "assembly_fingerprint", "plan_fingerprint", "capability_fingerprint",
        "model_switch_receipt", "delete_receipt", "mode_key", "input_mode", "duration", "seconds",
        "preferred_duration", "max_shots_per_segment", "generate_audio", "audio"}}
    parameters.update(resolution=chosen, aspect_ratio=ratio)
    digest = fingerprint({"plan_id": current["id"], "production_revision": production.revision,
                          "capability": capability.model_dump(mode="json"), "items": items, "blocked": blocked,
                          "parameters": parameters, "materials": [r[3] for r in requirements]})
    return {"plan_id": current["id"], "production_revision": production.revision,
            "provider_model_id": provider_model_id, "resolution": chosen, "aspect_ratio": ratio,
            "requires_resolution_selection": not bool(chosen),
            "resolutions": available, "valid_resolutions": valid, "compatible_resolutions": compatible, "fingerprint": digest,
            "resolution_checks": [
                {"resolution": r, "changes": evaluated[r][1], "blocked": evaluated[r][2]}
                for r in available
            ],
            "changes": changes, "blocked": blocked, "segment_count": len(items), "model_called": False,
            "items": items, "parameters": parameters, "status": current["status"]}


async def apply(session, episode, *, request_id, expected_fingerprint, expected_plan_id,
                expected_production_revision, provider_model_id, resolution):
    operation = dict(request_id=request_id, expected_fingerprint=expected_fingerprint, expected_plan_id=expected_plan_id,
                     expected_production_revision=expected_production_revision, provider_model_id=provider_model_id, resolution=resolution)

    async def replay():
        rows = (await session.scalars(select(EpisodeProductionPlan).where(EpisodeProductionPlan.episode_id == episode.id))).all()
        for row in rows:
            receipt = (row.parameters or {}).get("model_switch_receipt")
            if receipt and receipt.get("request_id") == request_id:
                if receipt != operation:
                    raise ConflictError("请求编号已用于其他模型调整")
                return await get_active_plan(session, episode)
        return None

    recovered = await replay()
    if recovered:
        return recovered
    locked = await session.execute(update(EpisodeProduction).where(
        EpisodeProduction.episode_id == episode.id, EpisodeProduction.active_plan_id == expected_plan_id,
        EpisodeProduction.revision == expected_production_revision,
    ).values(revision=EpisodeProduction.revision).execution_options(synchronize_session=False))
    recovered = await replay()
    if recovered:
        return recovered
    if locked.rowcount != 1:
        raise ConflictError("制作稿或设置已变化，请重新检查；原设置未改变")
    data = await preview(session, episode, provider_model_id=provider_model_id, resolution=resolution)
    if data["fingerprint"] != expected_fingerprint:
        raise ConflictError("脚本、素材或模型规格已变化，请重新检查；未切换模型")
    if data["blocked"]:
        raise ConflictError(data["blocked"][0]["reason"])
    ids = set((await session.scalars(select(VideoSegment.id).where(VideoSegment.episode_id == episode.id))).all())
    active = (await session.scalars(select(Job).where(Job.project_id == episode.project_id,
        Job.status.in_(["queued", "running", "processing", "downloading", "retrying"])))).all()
    for job in active:
        payload = job.payload or {}
        if ((job.target_type == "video_segment" and job.target_id in ids) or
                payload.get("episode_id") == episode.id or (payload.get("segment_frame") or {}).get("segment_id") in ids):
            raise ConflictError(f"本集任务 #{job.id} 仍在执行，请完成或停止后切换模型")
    result = await create_plan(session, episode, expected_production_revision=expected_production_revision,
        provider_model_id=provider_model_id, model_capability_snapshot={}, parameters=data["parameters"],
        status=data["status"], segments=data["items"], parent_plan_id=expected_plan_id,
        model_switch_receipt=operation)
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id))
    production.settings = {**production.settings, "video_model_id": provider_model_id, "resolution": resolution, "aspect_ratio": "project"}
    await session.flush()
    return result
