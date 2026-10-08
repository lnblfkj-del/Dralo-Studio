"""Persistent prompt state, independent of image adoption and modal lifetime."""

import hashlib
import json

from app.services.asset_visual_identity import visual_profile

ACTIVE = {"queued", "running", "processing", "downloading", "retrying"}


def input_hash(snapshot):
    return hashlib.sha256(json.dumps(
        {key: snapshot.get(key) for key in
         ("id", "type", "name", "description", "attributes", "profile", "source_profile", "current_prompt")},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def asset_snapshot(asset, link):
    data = link.production_data or {}
    return {
        "id": asset.id, "type": asset.asset_type, "name": asset.name,
        "description": asset.description, "attributes": asset.attributes or {},
        "profile": visual_profile(asset.attributes, data, asset.description),
        "source_profile": data.get("profile") or {},
        "current_prompt": data.get("prompt_anchor", asset.prompt_anchor),
        "production_revision": link.production_revision,
    }


def prompt_state(asset, link, job):
    marker = (link.production_data or {}).get("prompt_optimization") or {}
    state = {"status": "pending", "job_id": None, "reason": None}
    if job is None or marker.get("job_id") != job.id:
        return state
    state["job_id"] = job.id
    if job.status in ACTIVE:
        state["status"] = "queued" if job.status == "queued" else "generating"
    elif marker.get("applied_hash") == input_hash(asset_snapshot(asset, link)):
        state["status"] = "optimized"
    elif marker.get("input_hash") != input_hash(asset_snapshot(asset, link)):
        return {**state, "status": "pending"}
    elif job.status in {"failed", "cancelled"}:
        state.update(status="failed", reason=job.error_message or "优化任务未完成")
    elif job.status == "succeeded":
        application = (job.result or {}).get("prompt_application") or {}
        reason = next((row.get("reason") for row in application.get("skipped", [])
                       if row.get("asset_id") == asset.id), "未写入优化结果")
        state.update(status="failed", reason=reason)
    return state
