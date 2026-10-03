"""Fail-closed MiniMax H3 segment video preflight and job creation."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Episode,
    Job,
    Project,
    Provider,
    ProviderModel,
    VideoSegment,
    VideoSegmentShot,
)
from app.services import h3_prompt_job_service
from app.services.h3_segment_authoring_service import load_h3_segment_source
from app.services.pricing_service import estimate as estimate_pricing
from app.services.production_snapshot_service import capture_script
from app.services.team_access import owner_scope
from app.services.video_prompt_freeze_service import build_video_prompt_freeze

PRODUCTION_VERSION = "h3_segment_video.v1"


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


async def _current(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, authoring_job_id: int, video_model_id: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud":
        from app.services.private_storage_service import h3_config
        await h3_config(session, owner_id, verify_connection=True)
    source = await load_h3_segment_source(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_id=segment_id, video_model_id=video_model_id, parameters=parameters,
    )
    from app.services.asset_service import get_project_asset_readiness
    from app.services.script_finalization_service import require_episode_production_ready

    episode = await session.get(Episode, episode_id)
    project = await session.get(Project, project_id)
    await require_episode_production_ready(session, episode)
    readiness = await get_project_asset_readiness(session, project)
    episode_assets = next((item for item in readiness["episodes"]
                           if item["episode_id"] == episode_id), None)
    if episode_assets and episode_assets["status"] not in {"ready", "no_requirements"}:
        raise ConflictError("H3 片段引用的角色或场景资产尚未准备好")
    model = await session.get(ProviderModel, video_model_id)
    if model is None or not model.enabled:
        raise ConflictError("H3 视频模型未启用")
    profile = source["profile"]
    if (profile.get("verification") != "channel_verified"
            or profile.get("production_enabled") is not True
            or not (profile.get("local_adapter_preflight") or {}).get("passed")):
        raise ConflictError("H3 当前模式尚未完成渠道认证并启用生产路由")
    job = await session.scalar(select(Job).where(
        Job.id == authoring_job_id, Job.project_id == project_id,
        Job.target_type == h3_prompt_job_service.TARGET_H3_PROMPT_AUTHORING,
        Job.target_id == segment_id, owner_scope(Job.owner_id, owner_id),
    ))
    if job is None:
        raise NotFoundError("H3 英文改写任务不存在")
    captured = (job.payload or {}).get("h3_authoring") or {}
    if (captured.get("video_model_id") != video_model_id
            or captured.get("source_plan_id") != source["plan_id"]
            or captured.get("source_plan_revision") != source["plan_revision"]
            or captured.get("source_parameters") != parameters):
        raise ConflictError("H3 英文改写与当前片段计划或生成参数不一致")
    prompt = h3_prompt_job_service.reviewed_h3_prompt(
        job, script=source["script"], profile=source["profile"],
        input_contract=source["input_contract"],
        project_style=source["project_style"],
        voice_guidance=source["voice_guidance"],
    )
    quote = estimate_pricing(model, prompt, source["input_contract"]["effective_parameters"])
    if quote.get("status") != "estimated" or quote.get("estimated_cents") is None:
        raise ConflictError("H3 视频模型价格未配置或未匹配当前时长、清晰度")
    from app.core.config import settings
    from app.services import private_storage_service
    from app.services.h3_media_staging_service import inspect_h3_references
    refs = source["input_contract"]["effective_references"]
    config = await private_storage_service.h3_config(session, owner_id, images=bool(refs)) if refs or settings.runtime_execution_location == "cloud" else None
    if refs:
        await inspect_h3_references(session, owner_id=owner_id, project_id=project_id,
                                    media_ids=[item["media_id"] for item in refs])
    storage_reference = private_storage_service.reference(config) if settings.runtime_execution_location == "cloud" else None
    core = {
        "version": PRODUCTION_VERSION,
        "project_id": project_id, "episode_id": episode_id, "segment_id": segment_id,
        "plan_id": source["plan_id"], "plan_revision": source["plan_revision"],
        "video_model_id": video_model_id, "authoring_job_id": authoring_job_id,
        "source_fingerprint": captured["contract"]["source_fingerprint"],
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "input": source["input_contract"],
        "profile": source["profile"],
        "pricing_version": quote["pricing_version"],
        "storage_reference": storage_reference,
    }
    return {"source": source, "prompt": prompt, "quote": quote,
            "storage_reference": storage_reference,
            "authoring_source_fingerprint": captured["contract"]["source_fingerprint"],
            "fingerprint": _digest(core)}


async def preflight_h3_segment_video(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, authoring_job_id: int, video_model_id: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    current = await _current(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_id=segment_id, authoring_job_id=authoring_job_id,
        video_model_id=video_model_id, parameters=parameters,
    )
    return {
        "fingerprint": current["fingerprint"],
        "plan_id": current["source"]["plan_id"],
        "plan_revision": current["source"]["plan_revision"],
        "input_mode": current["source"]["input_contract"]["input_mode"],
        "pricing_estimate": current["quote"],
        "requires_media_upload_confirmation": bool(
            current["source"]["input_contract"]["effective_references"]
        ),
    }


async def start_h3_segment_video(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, authoring_job_id: int, video_model_id: int,
    parameters: dict[str, Any], request_id: str, max_cost_cents: int,
    expected_fingerprint: str, confirm_media_upload: bool,
) -> Job:
    request_fingerprint = _digest({
        "project_id": project_id, "episode_id": episode_id, "segment_id": segment_id,
        "authoring_job_id": authoring_job_id, "video_model_id": video_model_id,
        "parameters": parameters, "max_cost_cents": max_cost_cents,
        "expected_fingerprint": expected_fingerprint,
        "confirm_media_upload": confirm_media_upload,
    })
    existing = (await session.scalars(select(Job).where(
        Job.project_id == project_id, Job.target_type == "video_segment",
        Job.target_id == segment_id, owner_scope(Job.owner_id, owner_id),
    ).order_by(Job.id.desc()))).all()
    for job in existing:
        if (job.payload or {}).get("request_id") == request_id:
            if (job.payload or {}).get("request_fingerprint") != request_fingerprint:
                raise ConflictError("相同请求编号的 H3 视频参数不一致")
            return job
    current = await _current(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_id=segment_id, authoring_job_id=authoring_job_id,
        video_model_id=video_model_id, parameters=parameters,
    )
    source, prompt = current["source"], current["prompt"]
    if current["fingerprint"] != expected_fingerprint:
        raise ConflictError("H3 来源、模型、报价或提示词已变化, 请重新预检")
    if current["quote"]["estimated_cents"] > max_cost_cents:
        raise ConflictError("H3 视频预估费用超过本次确认上限")
    refs = source["input_contract"]["effective_references"]
    if refs and not confirm_media_upload:
        raise ConflictError("H3 图片模式需要明确确认上传参考素材")
    segment = await session.get(VideoSegment, segment_id)
    if segment is None or segment.plan_id != source["plan_id"]:
        raise ConflictError("当前活动计划的片段已变化，请重新预检")
    active = await session.scalar(select(Job.id).where(
        Job.target_type == "video_segment", Job.target_id == segment_id,
        Job.status.in_(["queued", "running", "processing", "downloading", "retrying"]),
    ))
    if active is not None:
        raise ConflictError("该片段已有进行中的视频任务")
    from app.services.job_creation_service import create_video_job

    input_params = source["input_contract"]["effective_parameters"]
    video = await create_video_job(
        session, owner_id, provider_model_id=video_model_id, prompt=prompt,
        project_id=project_id, negative_prompt=None,
        first_frame_media_id=source["input_contract"]["first_frame_media_id"],
        last_frame_media_id=source["input_contract"]["last_frame_media_id"],
        reference_media_ids=source["input_contract"]["reference_media_ids"],
        parameters=input_params, h3_reviewed=True,
    )
    production_contract = {
        **video.payload["video_input_contract"], "ready": True,
        "blockers": [], "required_confirmations": [],
        "effective_references": video.payload["video_input_effective_references"],
        "effective_parameters": video.payload["parameters"],
    }
    if video.payload.get("object_storage_reference") != current["storage_reference"]:
        raise ConflictError("个人对象存储在创建任务期间已变化，请重新预检")
    if (production_contract["input_mode"] != source["input_contract"]["input_mode"]
            or production_contract["effective_references"] != refs
            or production_contract["effective_parameters"] != input_params):
        raise ConflictError("H3 正式视频输入与已审核改写的输入不同")
    script_snapshot = await capture_script(session, segment)
    if ((script_snapshot.content.get("parameters") or {}).get("structured_script")
            != source["script"]):
        raise ConflictError("H3 片段脚本在创建任务期间已变化")
    model = await session.get(ProviderModel, video_model_id)
    provider = await session.get(Provider, model.provider_id)
    freeze = build_video_prompt_freeze(
        provider, model, script=source["script"], input_contract=production_contract,
        active_prompt=prompt, voice_guidance=source["voice_guidance"],
        style_snapshot=None, selection="h3_reviewed_prompt",
    )
    shot_ids = list((await session.scalars(select(VideoSegmentShot.shot_id).where(
        VideoSegmentShot.segment_id == segment_id,
    ).order_by(VideoSegmentShot.order))).all())
    production_snapshot = {
        "version": PRODUCTION_VERSION,
        "authoring_job_id": authoring_job_id,
        "source_fingerprint": current["authoring_source_fingerprint"],
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "profile": source["profile"],
        "confirm_media_upload": confirm_media_upload,
        "preflight_fingerprint": expected_fingerprint,
        "object_storage_reference": current["storage_reference"],
    }
    production_snapshot["fingerprint"] = _digest(production_snapshot)
    video.target_type = "video_segment"
    video.target_id = segment_id
    video.payload = {
        **video.payload, "request_id": request_id,
        "request_fingerprint": request_fingerprint,
        "confirmed_max_cost_cents": max_cost_cents,
        "script_snapshot_id": script_snapshot.id,
        "script_fingerprint": script_snapshot.fingerprint,
        "script_snapshot": script_snapshot.content,
        "plan_id": segment.plan_id, "episode_id": episode_id,
        "segment_order": segment.order,
        "generation_duration": segment.generation_duration,
        "shot_ids": shot_ids,
        "style_snapshot": None,
        "video_prompt_freeze": freeze,
        "h3_production": production_snapshot,
    }
    from app.services.segment_video_candidate_service import attach_input_evidence

    attach_input_evidence(video)
    segment.status = "generating"
    await session.flush()
    return video


def assert_h3_video_job(job: Job, model: ProviderModel) -> None:
    production = (job.payload or {}).get("h3_production")
    if (job.target_type != "video_segment" or not isinstance(production, dict)
            or production.get("version") != PRODUCTION_VERSION):
        raise ConflictError("H3 视频任务缺少来源审核与生产快照，未提交收费视频任务")
    if production.get("fingerprint") != _digest({
        key: value for key, value in production.items() if key != "fingerprint"
    }):
        raise ConflictError("H3 生产快照已变化，未提交收费视频任务")
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud" and (
        not job.payload.get("object_storage_reference")
        or job.payload["object_storage_reference"] != production.get("object_storage_reference")
    ):
        raise ConflictError("H3 任务对象存储版本与已确认快照不一致，未提交收费视频任务")
    prompt = job.payload.get("prompt")
    if (not isinstance(prompt, str) or sha256(prompt.encode()).hexdigest() != production.get("prompt_sha256")
            or job.model != model.model_id or job.provider_id != model.provider_id
            or job.cost_estimate is None
            or job.cost_estimate > job.payload.get("confirmed_max_cost_cents", -1)):
        raise ConflictError("H3 提示词、模型或确认费用与任务快照不一致")
    quote = job.payload.get("pricing_snapshot") or {}
    if (quote.get("status") != "estimated" or quote.get("estimated_cents") != job.cost_estimate
            or quote.get("provider_model_id") != model.id):
        raise ConflictError("H3 任务报价快照无效，未提交收费视频任务")
    contract = job.payload.get("video_input_contract") or {}
    if contract.get("protocol") != "minimax_video_v2" or contract.get("preview_only"):
        raise ConflictError("H3 任务没有正式的视频输入合同")
    if (job.payload.get("reference_media_ids") or job.payload.get("first_frame_media_id")
            or job.payload.get("last_frame_media_id")) and production.get("confirm_media_upload") is not True:
        raise ConflictError("H3 图片素材上传未经确认")
