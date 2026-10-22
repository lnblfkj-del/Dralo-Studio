"""Read-only options for an edited segment, using the production mode matcher."""

import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError
from app.models import EpisodeProduction, MediaFile, Project
from app.services.asset_production_core import fingerprint
from app.services.episode_planning_capability import load_capability
from app.services.job_video_batch_helpers import _compile_segment_media
from app.services.segment_asset_references import resolve_materials
from app.services.segment_model_fit import content_needs, matching_modes
from app.services.segment_plan_core_service import get_active_plan
from app.services.video_model_settings import duration_values


class SegmentOptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: int = Field(gt=0)
    production_revision: int = Field(ge=0)
    segment_id: int = Field(gt=0)
    refs: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)
    generation_duration: float = Field(gt=0, le=600)
    shot_count: int = Field(ge=0, le=500)

    @field_validator("refs", "parameters")
    @classmethod
    def bounded_json(cls, value):
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 200_000:
            raise ValueError("Segment input is too large")
        return value

    @field_validator("refs")
    @classmethod
    def valid_refs(cls, value):
        for key in ("asset_references", "asset_bindings"):
            rows = value.get(key, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Invalid material references")
        ids = value.get("reference_media_ids", [])
        if not isinstance(ids, list) or len(ids) > 128 or any(type(v) is not int or v <= 0 for v in ids):
            raise ValueError("Invalid reference media IDs")
        continuity = value.get("continuity")
        if continuity is not None and (not isinstance(continuity, dict) or not isinstance(continuity.get("source_lineage_key"), str)):
            raise ValueError("Invalid continuity reference")
        return value

    @field_validator("parameters")
    @classmethod
    def valid_parameters(cls, value):
        for key in ("first_frame_media_id", "last_frame_media_id"):
            if value.get(key) is not None and (type(value[key]) is not int or value[key] <= 0):
                raise ValueError("Invalid frame media ID")
        script = value.get("structured_script") or {}
        if not isinstance(script, dict) or not isinstance(script.get("audio", {}), dict):
            raise ValueError("Invalid structured script")
        doc = script.get("editor_document") or {}
        if not isinstance(doc, dict) or not isinstance(doc.get("content", []), list):
            raise ValueError("Invalid script document")
        for paragraph in doc.get("content", []):
            if not isinstance(paragraph, dict) or not isinstance(paragraph.get("content", []), list):
                raise ValueError("Invalid script paragraph")
            for node in paragraph.get("content", []):
                if not isinstance(node, dict) or not isinstance(node.get("attrs", {}), dict) or not isinstance(node.get("text", ""), str):
                    raise ValueError("Invalid script node")
        return value


def options_for(capability, ratio, resolution, counts, needs, bgm, duration, shots):
    modes = matching_modes(capability, ratio, resolution, counts, needs, bgm)
    reason = ""
    if not any(m.aspect_ratio == ratio for m in capability.modes):
        reason = f"该模型不支持项目画幅 {ratio}，请选择其他模型"
    elif not any(m.aspect_ratio == ratio and m.resolution == resolution for m in capability.modes):
        reason = "该模型不支持当前清晰度，请在顶部选择清晰度"
    elif counts["last_frame"] and not counts["first_frame"]:
        reason = "已引用尾帧，但缺少首帧；请补充首帧或移除尾帧引用"
    elif not modes:
        reference_modes = matching_modes(capability, ratio, resolution, counts, (False, False, False), None)
        if reference_modes:
            reason = "当前素材组合不支持脚本所需的对白、音效或背景音乐设置，请更换模型或调整声音设置"
        else:
            reason = (f"当前清晰度不支持这些素材用途和数量：参考图 {counts['image']} 张、"
                      f"首帧 {counts['first_frame']} 张、尾帧 {counts['last_frame']} 张；请调整引用或更换模型")
    eligible = [m for m in modes if shots <= m.max_shots]
    if modes and not eligible:
        reason = f"当前素材组合每段最多 {max(m.max_shots for m in modes)} 个镜头，当前 {shots} 个；请拆分片段"
    durations = sorted({ms / 1000 for m in eligible for ms in duration_values(m.durations)})
    supported = duration in durations
    if durations and not supported:
        reason = f"当前素材组合不支持 {duration:g} 秒，请选择生成时长；脚本和引用已保留"
    return {"durations": durations, "input_modes": sorted({m.input_mode for m in eligible}),
            "current_supported": supported and not reason, "blocking_reason": reason or None}


async def inspect_options(session, episode, payload):
    current = await get_active_plan(session, episode)
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id))
    if not current or current["id"] != payload.plan_id or not production or production.revision != payload.production_revision:
        raise ConflictError("制作稿已变化，请重新读取；本地脚本未改动")
    segment = next((s for s in current["segments"] if s["id"] == payload.segment_id), None)
    if segment is None:
        raise NotFoundError("当前制作稿中没有该片段")
    project = await session.get(Project, episode.project_id)
    ratio = (project.creation_settings or {}).get("aspect_ratio")
    if not ratio or ratio == "default":
        raise ConflictError("请先在项目设置中确定画幅比例")
    cap = await load_capability(session, current["provider_model_id"])
    refs = await resolve_materials(session, project, payload.refs, strict=False)
    refs["shot_frames"] = (segment.get("refs") or {}).get("shot_frames", []) if payload.parameters == segment.get("parameters") else []
    states = refs.get("asset_materials", [])
    material_issue = next((s.get("message") or "引用素材待准备" for s in states
                           if s.get("material_status") == "invalid" or
                           (s.get("material_status") == "missing" and s.get("material_required"))), None)
    if material_issue:
        raise ConflictError(material_issue)
    effective = {**deepcopy(current["parameters"]), **deepcopy(payload.parameters)}
    first, last, images, bindings = _compile_segment_media(refs, effective)
    # Pinned bindings have already been checked against project-linked assets.
    bound = {b["media_file_id"] for b in bindings}
    for media_id in set(images) | {v for v in (first, last) if v}:
        if media_id in bound:
            continue
        media = await session.get(MediaFile, media_id)
        if not media or media.project_id != project.id or media.workspace_id != project.workspace_id or media.kind != "image":
            raise ConflictError("引用图片不属于当前项目或不可用，请重新选择素材")
    images = set(images)
    from app.services.style_generation_service import project_style_media

    style = await project_style_media(session, project.id, episode.owner_id, "video")
    if style and style not in {first, last}:
        images.add(style)
    unresolved = {a["asset_id"] for a in refs.get("asset_references", []) if a.get("material_required")
                  and not any(b.get("asset_id") == a["asset_id"] for b in bindings)}
    counts = {"image": len(images) + len(unresolved), "first_frame": int(bool(first or refs.get("continuity"))),
              "last_frame": int(bool(last))}
    resolution = effective.get("resolution") or (production.settings or {}).get("resolution")
    edited = {**segment, "parameters": payload.parameters}
    result = options_for(cap, ratio, resolution, counts, content_needs(edited),
                         (production.settings or {}).get("background_music"), payload.generation_duration, payload.shot_count)
    continuity = refs.get("continuity")
    if continuity:
        source = next((s for s in current["segments"] if s["lineage_key"] == continuity["source_lineage_key"]), None)
        if first:
            result.update(current_supported=False, blocking_reason="已指定首帧，不能同时使用上一片段尾帧；请选择一种首帧来源")
        elif not source or source["order"] >= segment["order"]:
            result.update(current_supported=False, blocking_reason="衔接来源必须是本集当前制作稿中的前序片段")
        elif not any(v.get("is_final") for v in source.get("video_versions", [])):
            result.update(current_supported=False, blocking_reason=f"前序片段 {source['order']:02d} 尚未采用视频，请先采用后再生成")
    if current["source_type"] == "content_frozen" and any(b.get("media_kind") in {"audio", "video"} for b in bindings):
        result.update(current_supported=False, blocking_reason="当前片段视频输入不支持音频或视频参考，请调整素材用途")
    return {**result, "plan_id": current["id"], "segment_id": segment["id"], "production_revision": production.revision,
            "provider_model_id": current["provider_model_id"], "aspect_ratio": ratio, "resolution": resolution,
            "references": counts, "config_fingerprint": fingerprint(cap.model_dump(mode="json")), "model_called": False}
