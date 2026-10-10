"""Resolve H3 authoring input from an active, owned production segment."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Project,
    Provider,
    ProviderModel,
    VideoSegment,
)
from app.providers.protocols import effective_protocol, validate_model_protocol
from app.services import h3_prompt_job_service
from app.services.h3_prompt_authoring import build_h3_authoring_contract
from app.services.job_video_batch_service import _compile_segment_media, _resolve_continuity_input
from app.services.segment_voice_guidance_service import compile_segment_voice_guidance
from app.services.style_generation_service import frozen_video_style_prompt, project_style_media
from app.services.team_access import owner_scope
from app.services.video_input_compiler import compile_video_input
from app.services.video_prompt_compiler import resolve_model_prompt_profile


async def load_h3_segment_source(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, video_model_id: int, parameters: dict[str, Any],
) -> dict[str, Any]:
    episode = await session.scalar(select(Episode).where(
        Episode.id == episode_id, Episode.project_id == project_id,
        Episode.status != "archived", owner_scope(Episode.owner_id, owner_id),
    ))
    project = await session.scalar(select(Project).where(
        Project.id == project_id, owner_scope(Project.owner_id, owner_id),
    ))
    if episode is None or project is None:
        raise NotFoundError("项目或分集不存在")
    production = await session.scalar(select(EpisodeProduction).where(
        EpisodeProduction.episode_id == episode_id,
    ))
    plan = await session.get(EpisodeProductionPlan, production.active_plan_id) if production else None
    if (plan is None or plan.status != "confirmed"
            or plan.source_script_revision != episode.script_revision):
        raise ConflictError("当前分集片段计划尚未确认或来源剧本已变化")
    if plan.source_type == "content_frozen":
        raise ConflictError("冻结计划的 H3 专用提交尚未完成接线，不能使用旧改写入口绕过冻结校验")
    segment = await session.scalar(select(VideoSegment).where(
        VideoSegment.id == segment_id, VideoSegment.episode_id == episode_id,
        VideoSegment.plan_id == plan.id, VideoSegment.status != "archived",
    ))
    if segment is None:
        raise NotFoundError("当前活动计划中不存在该片段")
    if plan.provider_model_id is not None and plan.provider_model_id != video_model_id:
        raise ConflictError("当前片段计划绑定了其他视频模型")
    pair = (await session.execute(
        select(ProviderModel, Provider).join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == video_model_id)
    )).one_or_none()
    if pair is None:
        raise NotFoundError("H3 视频模型不存在")
    model, provider = pair
    validate_model_protocol(provider, model)
    if (effective_protocol(provider, model) != "minimax_video_v2"
            or model.model_type != "video" or not provider.enabled):
        raise ConflictError("当前渠道不是可用的 MiniMax H3 V2 视频渠道")
    script = (segment.parameters or {}).get("structured_script")
    if not isinstance(script, dict) or not isinstance(script.get("camera"), list) or len(script["camera"]) != 1:
        raise ConflictError("H3 改写要求已保存的单镜头结构化片段脚本")
    if segment.negative_prompt and segment.negative_prompt.strip():
        raise ConflictError("H3 无独立负面提示词字段, 请先把限制写入片段脚本")

    refs = segment.refs or {}
    if refs.get("unmatched_assets") or refs.get("unresolved_assets"):
        raise ConflictError("片段资产尚未完成引用核对")
    from app.services.asset_binding_service import binding_role, resolve_asset_binding

    for binding in refs.get("asset_bindings", []):
        if not isinstance(binding, dict):
            raise ConflictError("片段资产绑定格式无效")
        try:
            asset_id = int(binding["asset_id"])
            asset_version_id = int(binding["asset_version_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError("片段资产绑定缺少有效的资产或版本编号") from exc
        await resolve_asset_binding(
            session, project, asset_id=asset_id,
            adoption_key=binding.get("adoption_key"),
            asset_version_id=asset_version_id,
            media_file_id=binding.get("media_file_id"), role=binding_role(binding),
        )
    effective = {**(plan.parameters or {}), **(segment.parameters or {}), **parameters,
                 "duration": segment.generation_duration}
    from app.services.audio_policy import video_context
    effective = await video_context(session, project, segment, provider, model, effective)
    project_ratio = (project.creation_settings or {}).get("aspect_ratio")
    if project_ratio and project_ratio not in {"default", "project"}:
        effective["aspect_ratio"] = project_ratio
    first, last, ordinary, _ = _compile_segment_media(refs, effective)
    continuity_frame, _ = await _resolve_continuity_input(session, segment, refs)
    if continuity_frame is not None:
        if first not in (None, continuity_frame):
            raise ConflictError("连续帧依赖与手工首帧冲突")
        first = continuity_frame
    images = [
        *([{"media_id": first, "role": "first_frame"}] if first else []),
        *([{"media_id": last, "role": "last_frame"}] if last else []),
        *[{"media_id": media_id, "role": "reference_image"} for media_id in ordinary],
    ]
    style_media_id = await project_style_media(session, project_id, owner_id, "video")
    if style_media_id and not any(row["media_id"] == style_media_id for row in images):
        images.append({"media_id": style_media_id, "role": "reference_image", "purpose": "project_style"})
    input_contract = compile_video_input(
        provider, model, images, effective, preview_only=True,
    )
    if not input_contract["ready"]:
        raise ConflictError("H3 输入未就绪: " + "; ".join(input_contract["blockers"]))
    supported = (model.default_params or {}).get("supported_video_input_modes") or []
    if input_contract["input_mode"] not in supported:
        raise ConflictError("此 H3 模型尚未配置并验证当前输入模式")
    profile = resolve_model_prompt_profile(provider, model, input_contract["input_mode"])
    project_style, _ = await frozen_video_style_prompt(session, project_id, owner_id, "")
    voice_guidance = await compile_segment_voice_guidance(session, project, segment, refs, policy=effective["audio_policy"])
    return {
        "project_id": project_id, "episode_id": episode_id, "segment_id": segment_id,
        "plan_id": plan.id, "plan_revision": plan.revision,
        "video_model_id": video_model_id, "parameters": parameters,
        "script": script, "profile": profile, "input_contract": input_contract,
        "project_style": project_style.strip(), "voice_guidance": voice_guidance,
    }


async def start_h3_authoring(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, video_model_id: int, text_model_id: int,
    parameters: dict[str, Any], request_id: str, max_cost_cents: int,
) -> Job:
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud":
        from app.services.private_storage_service import h3_config
        await h3_config(session, owner_id, verify_connection=True)
    source = await load_h3_segment_source(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_id=segment_id, video_model_id=video_model_id, parameters=parameters,
    )
    contract = build_h3_authoring_contract(
        source["script"], profile=source["profile"],
        input_contract=source["input_contract"],
        project_style=source["project_style"],
        voice_guidance=source["voice_guidance"],
    )
    existing = (await session.scalars(select(Job).where(
        Job.project_id == project_id, owner_scope(Job.owner_id, owner_id),
        Job.target_type == h3_prompt_job_service.TARGET_H3_PROMPT_AUTHORING,
        Job.target_id == segment_id,
    ).order_by(Job.id.desc()))).all()
    for job in existing:
        snapshot = (job.payload or {}).get("h3_authoring") or {}
        if snapshot.get("request_id") != request_id:
            continue
        if (snapshot.get("contract", {}).get("source_fingerprint") != contract["source_fingerprint"]
                or snapshot.get("video_model_id") != video_model_id
                or snapshot.get("max_cost_cents") != max_cost_cents
                or (job.payload or {}).get("provider_model_id") != text_model_id):
            raise ConflictError("相同请求编号的 H3 改写来源或模型已变化")
        return job
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud":
        from app.services.private_storage_service import h3_config
        await h3_config(session, owner_id, images=bool(source["input_contract"]["effective_references"]))
    job = await h3_prompt_job_service.create_h3_prompt_job(
        session, owner_id, project_id=project_id, provider_model_id=text_model_id,
        segment_id=segment_id, video_model_id=video_model_id,
        source_plan_id=source["plan_id"], source_plan_revision=source["plan_revision"],
        source_parameters=parameters, script=source["script"],
        profile=source["profile"], input_contract=source["input_contract"],
        project_style=source["project_style"], voice_guidance=source["voice_guidance"],
    )
    if job.cost_estimate is None or job.cost_estimate > max_cost_cents:
        raise ConflictError("H3 英文改写文本模型价格未知或超过本次确认上限")
    snapshot = job.payload["h3_authoring"]
    job.payload = {**job.payload, "h3_authoring": {
        **snapshot, "request_id": request_id, "max_cost_cents": max_cost_cents,
    }}
    await session.flush()
    return job


async def review_h3_segment_authoring(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    segment_id: int, job_id: int,
) -> dict[str, Any]:
    job = await session.scalar(select(Job).where(
        Job.id == job_id, Job.project_id == project_id,
        Job.target_type == h3_prompt_job_service.TARGET_H3_PROMPT_AUTHORING,
        Job.target_id == segment_id, owner_scope(Job.owner_id, owner_id),
    ))
    if job is None:
        raise NotFoundError("H3 改写任务不存在")
    snapshot = job.payload["h3_authoring"]
    source = await load_h3_segment_source(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_id=segment_id, video_model_id=snapshot["video_model_id"],
        parameters=snapshot["source_parameters"],
    )
    if (source["plan_id"] != snapshot["source_plan_id"]
            or source["plan_revision"] != snapshot["source_plan_revision"]):
        raise ConflictError("片段计划修订已变化, 请重新生成 H3 改写")
    return await h3_prompt_job_service.review_h3_prompt_job(
        session, job, reviewer_id=owner_id, script=source["script"],
        profile=source["profile"], input_contract=source["input_contract"],
        project_style=source["project_style"], voice_guidance=source["voice_guidance"],
    )
