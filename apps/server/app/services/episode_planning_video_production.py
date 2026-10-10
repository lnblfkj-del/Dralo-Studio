"""Paid video preparation from activated frozen content, using existing jobs."""

import asyncio
from hashlib import sha256
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError, ConflictError
from app.models import (
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Provider,
    ProviderModel,
    SegmentVideoVersion,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.models.episode_planning import EpisodePlanningRecord
from app.providers.protocols import execution_contract
from app.services import episode_planning_workflow as workflow
from app.services.asset_production_core import fingerprint
from app.services.episode_planning_activation import verify_shots
from app.services.episode_planning_projection import read_projection
from app.services.episode_planning_storage import read_content_analysis, read_plan
from app.services.episode_planning_video_preview import adapter_view, compile_preview
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing
from app.services.pricing_service import attach as attach_pricing
from app.services.pricing_service import estimate
from app.services.production_snapshot_service import build_script_snapshot_content, capture_script
from app.services.style_generation_service import frozen_video_style_prompt
from app.services.video_prompt_freeze_service import build_video_prompt_freeze


def probe_image(media, protocol=None):
    root = settings.storage_path.resolve()
    path = (root / media["file_path"]).resolve()
    try:
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or path.stat().st_size > 20 * 1024 * 1024
        ):
            raise ConflictError("冻结参考图片不存在或超过 20 MB，请重新核对素材")
        with path.open("rb") as file:
            data = file.read(20 * 1024 * 1024 + 1)
    except OSError as exc:
        raise ConflictError("参考图片暂时无法读取，请核对存储后重试预检") from exc
    if len(data) > 20 * 1024 * 1024:
        raise ConflictError("参考图片超过 20 MB，请重新核对素材")
    if sha256(data).hexdigest() != media["hash"]:
        raise ConflictError("参考图片文件与冻结指纹不一致，请重新核对素材")
    try:
        with Image.open(BytesIO(data)) as image:
            size, format_name = image.size, image.format
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ConflictError("参考图片无法解码，请重新导入") from exc
    if format_name not in {"PNG", "JPEG", "WEBP"} or size != (media["width"], media["height"]):
        raise ConflictError("参考图片格式或尺寸与冻结记录不一致，请重新核对")
    if protocol == "meaicc_video_images":
        from base64 import b64encode

        from app.providers.meaicc_media import decode_image

        mime = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(format_name, "image/webp")
        decode_image(f"data:{mime};base64,{b64encode(data).decode('ascii')}")
    return len(data)


async def context(session, plan):
    if plan is None or plan.source_type != "content_frozen":
        raise ConflictError("冻结计划不存在，请刷新制作台")
    parent = await session.get(Job, plan.parameters.get("planning_run_id"))
    if parent is None:
        raise ConflictError("冻结计划缺少来源任务")
    parent = await workflow._lock_parent(session, parent.id)
    projected = await read_projection(
        session, parent, expected_fingerprint=plan.parameters["assembly_fingerprint"]
    )
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == plan.episode_id)
    )
    if (
        production is None
        or production.active_plan_id != plan.id
        or projected["id"] != plan.id
        or plan.status != "confirmed"
    ):
        raise ConflictError("冻结计划尚未启用或已经替换，请刷新制作台")
    record = await session.get(EpisodePlanningRecord, plan.parameters["planning_record_id"])
    frozen = read_plan(record)
    await verify_shots(session, plan, parent.result["assembly"])
    model = await session.get(ProviderModel, frozen.capability.provider_model_id)
    provider = await session.get(Provider, model.provider_id)
    preview = compile_preview(
        frozen,
        read_content_analysis(record),
        parent.result["assembly"],
        provider=provider,
        model=model,
        current_capability=frozen.capability,
        include_contract=True,
    )
    return parent, model, provider, preview


async def prepare_segment(session, plan, segment, parent, model, provider, compiled):
    if compiled["blockers"]:
        raise ConflictError("；".join(compiled["blockers"]))
    if compiled["input_contract"] is None or compiled["prompt"] is None:
        raise ConflictError("该模式尚未完成视频编译认证")
    if compiled["input_contract"]["protocol"] == "minimax_video_v2":
        raise ConflictError("该模式需专用媒体上传及请求审核，目前尚未完成冻结计划接线")
    contract = compiled["input_contract"]
    ids = {ref["media_id"] for ref in contract["effective_references"]}
    snapshots = {
        item["media"]["id"]: item["media"] for item in parent.payload["reference_snapshot"]
    }
    total = 0
    for media_id in ids:
        if media_id not in snapshots:
            raise ConflictError("视频编译引入了未冻结的参考素材")
        total += await asyncio.to_thread(probe_image, snapshots[media_id], contract["protocol"])
    if total > 20 * 1024 * 1024:
        raise ConflictError("本片段参考图片总大小超过 20 MB")
    prompt, style = await frozen_video_style_prompt(
        session, parent.project_id, plan.owner_id, compiled["prompt"]
    )
    if style and style["media_id"] and style["media_id"] not in contract["reference_media_ids"]:
        raise ConflictError("项目风格图不在冻结参考素材中，请显式绑定后重新规划")
    from app.services.video_input_compiler import prepare_video_prompt

    prompt, negative = prepare_video_prompt(provider, adapter_view(model), prompt, None)
    freeze = build_video_prompt_freeze(
        provider,
        adapter_view(model),
        script=compiled["structured_script"],
        input_contract=contract,
        active_prompt=prompt,
        style_snapshot=style,
        selection="model_compiler",
    )
    content = await build_script_snapshot_content(session, segment)
    return {
        "prompt": prompt,
        "negative_prompt": negative,
        "style_snapshot": style,
        "contract": contract,
        "video_prompt_freeze": freeze,
        "script_fingerprint": fingerprint(content),
        "quote": estimate(model, prompt, contract["effective_parameters"]),
    }


def validate_parameters(parameters, compiled):
    if set(parameters) - {"duration", "aspect_ratio", "resolution"} or any(
        parameters.get(key, compiled["parameters"][key]) != compiled["parameters"][key]
        for key in ("duration", "aspect_ratio", "resolution")
    ):
        raise ConflictError("生成设置与冻结计划不同，请在整集规划中检查模型规格")


async def build_plan(session, episode, plan, model_id, parameters, regenerate, business_executor):
    if model_id != plan.provider_model_id:
        raise ConflictError("当前冻结计划属于其他模型，请先检查兼容性并切换规划")
    parent, model, provider, preview = await context(session, plan)
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(VideoSegment.plan_id == plan.id)
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    final_ids = set(
        (
            await session.scalars(
                select(SegmentVideoVersion.segment_id).where(
                    SegmentVideoVersion.segment_id.in_([s.id for s in segments]),
                    SegmentVideoVersion.is_final.is_(True),
                )
            )
        ).all()
    )
    active_ids = set(
        (
            await session.scalars(
                select(Job.target_id).where(
                    Job.target_type == "video_segment",
                    Job.target_id.in_([s.id for s in segments]),
                    Job.status.not_in(TERMINAL_STATUSES),
                )
            )
        ).all()
    )
    eligible, skipped, blocked, quotes, inputs, all_shots = [], [], [], [], [], []
    for segment, compiled in zip(segments, preview["segments"], strict=True):
        shots = list(
            (
                await session.scalars(
                    select(Shot)
                    .join(VideoSegmentShot, VideoSegmentShot.shot_id == Shot.id)
                    .where(VideoSegmentShot.segment_id == segment.id)
                    .order_by(VideoSegmentShot.order)
                )
            ).all()
        )
        shot_ids = [s.id for s in shots]
        all_shots.extend(shot_ids)
        if segment.id in final_ids and not regenerate:
            skipped.append(segment.id)
            continue
        try:
            if segment.id in active_ids or segment.status == "generating":
                raise ConflictError("该片段已有进行中的视频任务")
            if segment.status == "archived" or any(s.is_locked for s in shots):
                raise ConflictError("该片段已归档或镜头已锁定")
            validate_parameters(parameters, compiled)
            prepared = await prepare_segment(
                session, plan, segment, parent, model, provider, compiled
            )
        except AppError as exc:
            blocked.append(
                {
                    "segment_id": segment.id,
                    "order": segment.order,
                    "shot_ids": shot_ids,
                    "reason": exc.message,
                }
            )
            continue
        quotes.append(
            {
                **prepared["quote"],
                "segment_id": segment.id,
                "order": segment.order,
                "generation_duration": segment.generation_duration,
                "shot_ids": shot_ids,
            }
        )
        inputs.append(
            {
                "segment_id": segment.id,
                "fingerprint": prepared["contract"]["fingerprint"],
                "video_prompt_freeze": prepared["video_prompt_freeze"],
                "continuity_dependency": None,
                "sound_input": {},
                "script_fingerprint": prepared["script_fingerprint"],
                "input_mode": prepared["contract"]["input_mode"],
                "protocol": prepared["contract"]["protocol"],
            }
        )
        eligible.append(segment.id)
    return {
        "project_id": episode.project_id,
        "episode_id": episode.id,
        "episode_number": episode.number,
        "provider_model_id": model.id,
        "provider_name": provider.name,
        "model_id": model.model_id,
        "plan_id": plan.id,
        "plan_version": plan.version,
        "plan_revision": plan.revision,
        "eligible_segment_ids": eligible,
        "skipped_final_segment_ids": skipped,
        "eligible_shot_ids": [shot for item in quotes for shot in item["shot_ids"]],
        "skipped_final_shot_ids": [],
        "blocked": blocked,
        "total_segments": len(segments),
        "total_shots": len(all_shots),
        "total_generation_duration": sum(
            s.generation_duration for s in segments if s.id in eligible
        ),
        "estimated_count": len(quotes),
        "pricing_estimate": _aggregate_video_pricing(quotes),
        "video_inputs": inputs,
        "requires_confirmation": True,
        "business_executor": business_executor,
    }


async def create_job(
    session, owner_id, segment, *, project_id, provider_model_id, parameters, shot_ids
):
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    parent, model, provider, preview = await context(session, plan)
    if await session.scalar(
        select(Job.id)
        .where(
            Job.target_type == "video_segment",
            Job.target_id == segment.id,
            Job.status.not_in(TERMINAL_STATUSES),
        )
        .limit(1)
    ):
        raise ConflictError("该片段已有进行中的视频任务")
    compiled = next(s for s in preview["segments"] if s["segment_key"] == segment.lineage_key)
    if provider_model_id != model.id:
        raise ConflictError("视频模型与冻结计划不同")
    if project_id != parent.project_id:
        raise ConflictError("视频项目与冻结来源不同")
    validate_parameters(parameters, compiled)
    linked_ids = list(
        (
            await session.scalars(
                select(VideoSegmentShot.shot_id)
                .where(VideoSegmentShot.segment_id == segment.id)
                .order_by(VideoSegmentShot.order)
            )
        ).all()
    )
    if shot_ids != linked_ids:
        raise ConflictError("视频镜头列表与冻结计划不同")
    prepared = await prepare_segment(session, plan, segment, parent, model, provider, compiled)
    snapshot = await capture_script(session, segment)
    contract = prepared["contract"]
    public_contract = {
        k: v
        for k, v in contract.items()
        if k
        not in {
            "effective_references",
            "effective_parameters",
            "actions",
            "blockers",
            "ready",
            "required_confirmations",
            "audio_policy",
        }
    }
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        workspace_id=segment.workspace_id,
        provider_id=provider.id,
        job_type="video",
        status="queued",
        target_type="video_segment",
        target_id=segment.id,
        provider=provider.name,
        model=model.model_id,
        max_attempts=1,
        payload={
            "protocol_contract": execution_contract(provider, model),
            "provider_model_id": model.id,
            "prompt": prepared["prompt"],
            "negative_prompt": prepared["negative_prompt"],
            "style_snapshot": prepared["style_snapshot"],
            "video_input_contract": public_contract,
            "video_input_effective_references": contract["effective_references"],
            "video_input_actions": contract["actions"],
            "parameters": contract["effective_parameters"],
            "first_frame_media_id": contract["first_frame_media_id"],
            "last_frame_media_id": contract["last_frame_media_id"],
            "reference_media_ids": contract["reference_media_ids"],
            "video_prompt_freeze": prepared["video_prompt_freeze"],
            "script_snapshot": snapshot.content,
            "script_snapshot_id": snapshot.id,
            "script_fingerprint": snapshot.fingerprint,
            "plan_id": plan.id,
            "episode_id": segment.episode_id,
            "segment_order": segment.order,
            "shot_ids": shot_ids,
            "generation_duration": segment.generation_duration,
            "continuity_dependency": None,
            "sound_input": {},
            "content_planning_run_id": parent.id,
        },
    )
    attach_pricing(job, model)
    from app.services.segment_video_candidate_service import attach_input_evidence

    attach_input_evidence(job)
    session.add(job)
    segment.status = "generating"
    await session.flush()
    return job


async def validate_execution(session, job):
    if (job.payload.get("video_submission") or {}).get("started"):
        return
    segment = await session.get(VideoSegment, job.target_id)
    if segment is None:
        raise ConflictError("冻结视频片段已删除")
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    parent, model, provider, preview = await context(session, plan)
    compiled = next(s for s in preview["segments"] if s["segment_key"] == segment.lineage_key)
    prepared = await prepare_segment(session, plan, segment, parent, model, provider, compiled)
    if (
        prepared["video_prompt_freeze"]["fingerprint"]
        != job.payload["video_prompt_freeze"]["fingerprint"]
        or prepared["script_fingerprint"] != job.payload["script_fingerprint"]
    ):
        raise ConflictError("视频来源在排队后发生变化，已停止收费提交")
