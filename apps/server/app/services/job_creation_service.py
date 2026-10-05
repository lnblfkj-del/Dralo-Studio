"""Job 创建服务：文本、图片、视频任务的创建与批量提交。

本模块处理所有类型 Job 的创建逻辑，包括参数校验、协议适配和批量任务构建。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import String, and_, case, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXPORT,
    JOB_TYPE_IMAGE,
    JOB_TYPE_TEXT,
    JOB_TYPE_VIDEO,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    MediaFile,
    Project,
    Provider,
    ProviderModel,
    Scene,
    SegmentVideoVersion,
    Shot,
    ShotVideoVersion,
    VideoSegment,
    VideoSegmentShot,
    utcnow,
)
from app.providers.protocols import execution_contract, is_toapis_model, validate_model_protocol
from app.providers.video_contracts import VIDEO_CONTRACTS, validate_video_parameters
from app.services.job_concurrency_service import BATCH_PARENT_TARGETS, TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing, _video_model_pair
from app.services.pricing_service import attach as attach_pricing
from app.services.pricing_service import estimate as estimate_pricing
from app.services.team_access import owner_scope


async def create_text_job(
    session: AsyncSession,
    owner_id: int,
    *,
    provider_model_id: int,
    prompt: str,
    project_id: int | None,
    parameters: dict[str, Any],
) -> Job:
    row = await session.execute(
        select(ProviderModel, Provider)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == provider_model_id)
    )
    pair = row.one_or_none()
    if pair is None:
        raise NotFoundError("模型不存在")
    model, provider = pair
    validate_model_protocol(provider, model)
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != "text":
        raise ConflictError("M2 文本任务只能使用文本模型")
    if project_id is not None:
        project = await session.scalar(
            select(Project.id).where(Project.id == project_id, owner_scope(Project.owner_id, owner_id))
        )
        if project is None:
            raise NotFoundError("项目不存在")
    from app.services import execution_policy_service, text_model_policy_service

    execution_snapshot = execution_policy_service.current_snapshot()
    effective_parameters, text_policy = text_model_policy_service.resolve(
        model, provider, parameters, execution_snapshot
    )
    execution_snapshot["text_model"] = text_policy
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        provider_id=provider.id,
        job_type=JOB_TYPE_TEXT,
        status=JOB_STATUS_QUEUED,
        payload={"provider_model_id": model.id, "prompt": prompt, "parameters": effective_parameters, "protocol_contract": execution_contract(provider, model)},
        execution_policy_snapshot=execution_snapshot,
        provider=provider.name,
        model=model.model_id,
    )
    attach_pricing(job, model)
    session.add(job)
    await session.flush()
    return job


async def create_image_job(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int | None,
    asset_id: int,
    provider_model_id: int,
    prompt: str,
    negative_prompt: str | None,
    reference_media_ids: list[int],
    parameters: dict[str, Any],
    view_type: str = "base",
    view_label: str | None = None,
) -> Job:
    row = await session.execute(
        select(ProviderModel, Provider)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == provider_model_id)
    )
    pair = row.one_or_none()
    if pair is None:
        raise NotFoundError("模型不存在")
    model, provider = pair
    validate_model_protocol(provider, model)
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != "image":
        raise ConflictError("图片任务只能使用图片模型")
    from app.models import Asset
    asset = await session.get(Asset, asset_id)
    if asset is not None and asset.asset_type == "voice":
        raise ConflictError("声音资产不能生成图片，请使用声音生成入口")
    requested_ratio = parameters.get("aspect_ratio")
    parameters = {**model.default_params, **parameters}
    if project_id is not None and requested_ratio in (None, "", "default"):
        ratio_project = await session.get(Project, project_id)
        ratio = (ratio_project.creation_settings or {}).get("aspect_ratio") if ratio_project else None
        parameters["aspect_ratio"] = ratio if ratio and ratio != "default" else "16:9"
    if (model.api_protocol or provider.protocol) == "openai_compatible":
        from app.providers.image_parameters import openai_image_parameters
        openai_image_parameters(model.model_id, parameters)
    from app.services.asset_identity_input import costume_identity
    prompt, reference_media_ids = await costume_identity(
        session, project_id, asset_id, model, prompt, reference_media_ids
    )
    if project_id is not None:
        project = await session.scalar(
            select(Project.id).where(Project.id == project_id, owner_scope(Project.owner_id, owner_id))
        )
        if project is None:
            raise NotFoundError("项目不存在")
    from app.services.asset_visual_identity import image_identity
    prompt, identity_snapshot = await image_identity(
        session, await session.get(Project, project_id) if project_id else None, asset, prompt
    )
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        provider_id=provider.id,
        job_type=JOB_TYPE_IMAGE,
        target_type="asset",
        target_id=asset_id,
        status=JOB_STATUS_QUEUED,
        payload={
            "protocol_contract": execution_contract(provider, model),
            "provider_model_id": model.id,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "reference_media_ids": reference_media_ids,
            "view_type": view_type,
            "view_label": view_label,
            "visual_identity_snapshot": identity_snapshot,
            "parameters": {**model.default_params, **parameters},
        },
        provider=provider.name,
        model=model.model_id,
    )
    attach_pricing(job, model)
    session.add(job)
    await session.flush()
    return job


async def create_video_job(
    session: AsyncSession,
    owner_id: int,
    *,
    provider_model_id: int,
    prompt: str,
    project_id: int,
    negative_prompt: str | None,
    first_frame_media_id: int | None,
    last_frame_media_id: int | None,
    reference_media_ids: list[int],
    parameters: dict[str, Any],
    h3_reviewed: bool = False,
) -> Job:
    row = await session.execute(
        select(ProviderModel, Provider).join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == provider_model_id)
    )
    pair = row.one_or_none()
    if pair is None:
        raise NotFoundError("模型不存在")
    model, provider = pair
    validate_model_protocol(provider, model)
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != "video":
        raise ConflictError("视频任务只能使用视频模型")
    from app.providers.protocols import effective_protocol
    if effective_protocol(provider, model) == "minimax_video_v2" and not h3_reviewed:
        raise ConflictError("MiniMax H3 此入口生产提交尚未开放；请先完成片段来源绑定和英文提示词审核")
    storage_reference = None
    if effective_protocol(provider, model) == "minimax_video_v2":
        from app.core.config import settings
        from app.services import private_storage_service
        images = bool(first_frame_media_id or last_frame_media_id or reference_media_ids)
        config = await private_storage_service.h3_config(session, owner_id, images=images) if images or settings.runtime_execution_location == "cloud" else None
        if settings.runtime_execution_location == "cloud":
            storage_reference = private_storage_service.reference(config)
    parameters = dict(parameters or {})
    supplied_contract = parameters.pop("video_input_contract", None)
    raw_confirmations = parameters.pop("video_input_confirmations", [])
    confirmations = raw_confirmations if isinstance(raw_confirmations, list) else []
    from app.services.video_input_compiler import compile_video_input, prepare_video_prompt
    prompt, negative_prompt = prepare_video_prompt(provider, model, prompt, negative_prompt)
    references = [
        *([{"media_id": first_frame_media_id, "role": "first_frame"}] if first_frame_media_id else []),
        *([{"media_id": last_frame_media_id, "role": "last_frame"}] if last_frame_media_id else []),
        *[{"media_id": media_id, "role": "reference_image"} for media_id in reference_media_ids],
    ]
    from app.services.style_generation_service import project_style_media
    style_media_id = await project_style_media(session, project_id, owner_id, "video")
    if style_media_id and not any(item["media_id"] == style_media_id for item in references):
        references.append({"media_id": style_media_id, "role": "reference_image",
                           "purpose": "project_style"})
    compiled = compile_video_input(
        provider, model, references, parameters, negative_prompt=negative_prompt,
        confirmed_downgrades=confirmations,
        h3_authorized=h3_reviewed,
    )
    if not compiled["ready"]:
        raise ConflictError("视频输入协议校验未通过：" + "；".join(compiled["blockers"]))
    contract = {key: value for key, value in compiled.items() if key not in {
        "effective_references", "effective_parameters", "actions", "blockers", "ready", "required_confirmations"
    }}
    if supplied_contract and supplied_contract.get("fingerprint") != contract["fingerprint"]:
        raise ConflictError("视频输入协议已变化，请重新预检")
    first_frame_media_id = contract["first_frame_media_id"]
    last_frame_media_id = contract["last_frame_media_id"]
    reference_media_ids = contract["reference_media_ids"]
    parameters = compiled["effective_parameters"]
    project = await session.scalar(select(Project.id).where(
        Project.id == project_id, owner_scope(Project.owner_id, owner_id)
    ))
    if project is None:
        raise NotFoundError("项目不存在")
    from app.models import MediaFile
    media_ids = [x for x in [first_frame_media_id, last_frame_media_id, *reference_media_ids] if x]
    if media_ids:
        count = await session.scalar(select(func.count(MediaFile.id)).where(
            MediaFile.id.in_(set(media_ids)), owner_scope(MediaFile.owner_id, owner_id), MediaFile.kind == "image"
        ))
        if count != len(set(media_ids)):
            raise ConflictError("首帧、尾帧或参考媒体不存在")
    job = Job(
        owner_id=owner_id, project_id=project_id, provider_id=provider.id,
        job_type=JOB_TYPE_VIDEO, status=JOB_STATUS_QUEUED,
        target_type="project", target_id=project_id,
        payload={"protocol_contract": execution_contract(provider, model), "provider_model_id": model.id, "prompt": prompt,
                 "negative_prompt": negative_prompt, "first_frame_media_id": first_frame_media_id,
                 "last_frame_media_id": last_frame_media_id, "reference_media_ids": reference_media_ids,
                 "video_input_contract": contract, "video_input_actions": compiled["actions"],
                 "video_input_effective_references": compiled["effective_references"],
                 "parameters": parameters,
                 **({"object_storage_reference": storage_reference} if storage_reference else {})},
        provider=provider.name, model=model.model_id,
    )
    quote = attach_pricing(job, model)
    if (
        (model.pricing or {}).get("separate_audio_pricing")
        and parameters.get("audio") is True
        and quote["status"] == "unknown"
    ):
        raise ConflictError(quote["reason"])
    session.add(job)
    await session.flush()
    return job


_VIDEO_BATCH_EXPORTS = frozenset(
    {
        "create_shot_video_job",
        "create_segment_video_job",
        "create_episode_segment_jobs",
        "create_batch_video_jobs",
        "start_episode_video_batch",
        "start_segment_video_attempt",
        "start_project_episode_batches",
        "build_episode_video_plan",
        "build_segment_video_plan",
    }
)


def __getattr__(name: str) -> Any:
    if name in _VIDEO_BATCH_EXPORTS:
        from app.services import job_video_batch_service

        return getattr(job_video_batch_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def create_episode_export_job(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    request_id: str,
    expected_snapshot_fingerprint: str,
) -> Job:
    episode = await session.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.project_id == project_id,
            Episode.status != "archived",
            owner_scope(Episode.owner_id, owner_id),
        )
    )
    if episode is None:
        raise NotFoundError("分集不存在")
    from app.services.script_finalization_service import require_episode_production_ready

    await require_episode_production_ready(session, episode)
    existing_jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == "episode_export",
                    Job.target_id == episode_id,
                )
                .order_by(Job.id.desc())
            )
        ).all()
    )
    for existing in existing_jobs:
        if (existing.payload or {}).get("request_id") == request_id:
            return existing
    if any(existing.status not in TERMINAL_STATUSES for existing in existing_jobs):
        raise ConflictError("本集已有进行中的整集合成任务")
    preflight = await preflight_episode_export(
        session,
        owner_id,
        project_id=project_id,
        episode_id=episode_id,
    )
    if preflight["snapshot_fingerprint"] != expected_snapshot_fingerprint:
        raise ConflictError("整集合成来源已变化，请重新预检后提交")
    if preflight["status"] != "ready":
        raise ConflictError(preflight["issues"][0]["message"] if preflight["issues"] else "整集合成预检未通过")
    production = preflight["_production"]
    plan = preflight["_plan"]
    segments = preflight["_segments"]
    frozen_export = preflight["_snapshot"]
    settings = (production.settings if production else {}) or {}
    final_by_segment = preflight["_final_by_segment"]
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type=JOB_TYPE_EXPORT,
        status=JOB_STATUS_QUEUED,
        target_type="episode_export",
        target_id=episode.id,
        payload={
            "request_id": request_id,
            "episode_id": episode.id,
            "plan_id": plan.id,
            "plan_version": plan.version,
            "segment_video_version_ids": [
                final_by_segment[item.id].id for item in segments
            ],
            "production_snapshot": frozen_export,
            "snapshot_fingerprint": expected_snapshot_fingerprint,
            "background_audio_media_id": settings.get("background_audio_media_id"),
            "background_audio_volume": settings.get("background_audio_volume", 0.3),
            "include_subtitles": settings.get("include_subtitles", True),
            "pricing_snapshot": {
                "status": "estimated",
                "currency": "CNY",
                "amount": "0",
                "estimated_cents": 0,
                "reason": "本地 FFmpeg 合成，不调用外部生成渠道",
                "pricing_version": "local-export-v1",
            },
        },
        cost_estimate=0,
        max_attempts=1,
    )
    session.add(job)
    if production is not None:
        production.last_error = None
    await session.flush()
    return job


async def list_episode_export_versions(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
) -> list[dict[str, Any]]:
    episode = await session.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.project_id == project_id,
            Episode.status != "archived",
            owner_scope(Episode.owner_id, owner_id),
        )
    )
    if episode is None:
        raise NotFoundError("分集不存在")
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    jobs = list((await session.scalars(
        select(Job)
        .where(
            owner_scope(Job.owner_id, owner_id),
            Job.project_id == project_id,
            Job.target_type == "episode_export",
            Job.target_id == episode.id,
            Job.status == JOB_STATUS_SUCCEEDED,
        )
        .order_by(Job.finished_at, Job.id)
    )).all())
    media_ids = [
        int(job.result["media_file_id"])
        for job in jobs
        if isinstance(job.result, dict)
        and isinstance(job.result.get("media_file_id"), int)
        and int(job.result["media_file_id"]) > 0
    ]
    media_by_id = {
        item.id: item
        for item in (
            await session.scalars(
                select(MediaFile).where(
                    MediaFile.id.in_(media_ids),
                    MediaFile.project_id == project_id,
                    owner_scope(MediaFile.owner_id, owner_id),
                )
            )
        ).all()
    } if media_ids else {}
    versions: list[dict[str, Any]] = []
    for version, job in enumerate(jobs, start=1):
        result = job.result if isinstance(job.result, dict) else {}
        media_id = result.get("media_file_id")
        if not isinstance(media_id, int) or media_id <= 0:
            continue
        media = media_by_id.get(media_id)
        payload = job.payload if isinstance(job.payload, dict) else {}
        snapshot = payload.get("production_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        versions.append({
            "version": version,
            "job_id": job.id,
            "media_file_id": media_id,
            "media_url": f"/api/media/{media_id}",
            "original_name": media.original_name if media else None,
            "duration": media.duration if media else None,
            "width": media.width if media else None,
            "height": media.height if media else None,
            "size": media.size if media else None,
            "completed_at": job.finished_at or job.created_at,
            "is_current": bool(
                production and production.final_media_file_id == media_id
            ),
            "available": media is not None,
            "snapshot_fingerprint": payload.get("snapshot_fingerprint"),
            "plan_version": payload.get("plan_version"),
            "plan_revision": snapshot.get("plan_revision"),
            "output_spec": snapshot.get("output_spec") or {},
        })
    return list(reversed(versions))


async def preflight_episode_export(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
) -> dict[str, Any]:
    episode = await session.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.project_id == project_id,
            Episode.status != "archived",
            owner_scope(Episode.owner_id, owner_id),
        )
    )
    if episode is None:
        raise NotFoundError("分集不存在")
    from app.services.script_finalization_service import require_episode_production_ready

    await require_episode_production_ready(session, episode)
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    plan = (
        await session.get(EpisodeProductionPlan, production.active_plan_id)
        if production and production.active_plan_id
        else None
    )
    segments = list((await session.scalars(
        select(VideoSegment)
        .where(VideoSegment.plan_id == plan.id)
        .order_by(VideoSegment.order)
    )).all()) if plan else []
    issues: list[dict[str, Any]] = []
    from app.services.episode_edit_export_guard import EDIT_DRAFT_EXPORT_MESSAGE, pending_edit_draft

    pending_edit = await pending_edit_draft(session, episode.id)
    if pending_edit is not None:
        issues.append({"code": "EDIT_DRAFT_NOT_RENDERED", "message": EDIT_DRAFT_EXPORT_MESSAGE})
    if plan is None:
        issues.append({"code": "PLAN_MISSING", "message": "本集尚未建立片段生产计划，无法合成"})
    elif not segments:
        issues.append({"code": "SEGMENTS_MISSING", "message": "当前片段计划为空，无法合成"})
    final_versions = list((await session.scalars(
        select(SegmentVideoVersion).where(
            SegmentVideoVersion.segment_id.in_([item.id for item in segments]),
            SegmentVideoVersion.is_final.is_(True),
        )
    )).all()) if segments else []
    final_by_segment = {item.segment_id: item for item in final_versions}
    for segment in segments:
        if segment.id not in final_by_segment:
            issues.append({
                "code": "FINAL_VERSION_MISSING",
                "message": f"片段 {segment.order} 尚未选定最终视频版本",
                "segment_id": segment.id,
            })
    frozen_export: dict[str, Any] | None = None
    if not issues:
        from app.services.production_snapshot_service import export_snapshot

        try:
            frozen_export = await export_snapshot(session, episode, segments, final_by_segment)
        except AppError as exc:
            issues.append({"code": "SNAPSHOT_INVALID", "message": exc.message})
    timeline_cursor = 0.0
    segment_rows = []
    for segment in segments:
        version = final_by_segment.get(segment.id)
        duration = float(segment.timeline_duration)
        segment_rows.append({
            "segment_id": segment.id,
            "order": segment.order,
            "title": segment.title,
            "video_version_id": version.id if version else None,
            "media_file_id": version.media_file_id if version else None,
            "trim_in": float(segment.trim_in),
            "timeline_duration": duration,
            "timeline_start": timeline_cursor,
            "timeline_end": timeline_cursor + duration,
        })
        timeline_cursor += duration
    settings = (production.settings if production else {}) or {}
    fingerprint_payload = {
        "episode_id": episode.id,
        "plan_id": plan.id if plan else None,
        "plan_version": plan.version if plan else None,
        "plan_revision": plan.revision if plan else None,
        "production_revision": production.revision if production else 0,
        "segments": segment_rows,
        "snapshot": frozen_export,
        "pending_edit": pending_edit,
        "issues": issues,
    }
    snapshot_fingerprint = sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    sound_cues = (frozen_export or {}).get("sound_cues", settings.get("sound_cues") or [])
    dialogue_cues = (frozen_export or {}).get("dialogue_cues", settings.get("dialogue_cues") or [])
    result = {
        "status": "blocked" if issues else "ready",
        "episode_id": episode.id,
        "plan_id": plan.id if plan else None,
        "plan_version": plan.version if plan else None,
        "plan_revision": plan.revision if plan else None,
        "production_revision": production.revision if production else 0,
        "snapshot_fingerprint": snapshot_fingerprint,
        "total_duration": timeline_cursor,
        "segments": segment_rows,
        "output_spec": (frozen_export or {}).get("output_spec", {}),
        "subtitle_count": len((frozen_export or {}).get("subtitle_source", [])),
        "dialogue_audio_count": sum(1 for item in dialogue_cues if item.get("audio_media_id")),
        "background_music": settings.get("background_audio_media_id") is not None,
        "ambience_count": sum(1 for item in sound_cues if item.get("kind") == "ambience"),
        "sfx_count": sum(1 for item in sound_cues if item.get("kind") == "sfx"),
        "audio_mix_order": (frozen_export or {}).get("audio_mix_order", [
            "native", "background_music", "ambience", "dialogue", "sfx",
        ]),
        "issues": issues,
        "requires_confirmation": True,
        "_production": production,
        "_plan": plan,
        "_segments": segments,
        "_final_by_segment": final_by_segment,
        "_snapshot": frozen_export,
    }
    return result
