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
    Project,
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
from app.services.job_video_batch_helpers import _sound_input_summary
from app.services.job_pricing_service import _aggregate_video_pricing
from app.services.pricing_service import attach as attach_pricing
from app.services.pricing_service import estimate
from app.services.production_snapshot_service import build_script_snapshot_content, capture_script
from app.services.style_generation_service import frozen_video_style_prompt, project_style_media
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


async def context(session, plan, *, resolve_assets=True, selected_segment_ids=None, execution=None):
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
    if execution is None:
        model = await session.get(ProviderModel, frozen.capability.provider_model_id)
        provider = await session.get(Provider, model.provider_id)
        current_capability = await workflow.load_capability(session, model.id)
    else:
        provider, model = execution
        from app.services.episode_planning_capability import capability_for
        current_capability = capability_for(provider, model)
    material_references = None
    materials = {}
    if resolve_assets:
        from app.services.segment_asset_references import catalog, resolve_materials
        project = await session.get(Project, parent.project_id)
        assets = await catalog(session, project.id)
        segments = (await session.scalars(select(VideoSegment).where(VideoSegment.plan_id == plan.id))).all()
        for segment in segments:
            if selected_segment_ids is not None and segment.id not in selected_segment_ids:
                continue
            materials[segment.lineage_key] = await resolve_materials(session, project, segment.refs, strict=False, assets=assets)
        material_references = {key: [
            {"media_id": binding["media_file_id"], "role": "image" if binding["role"] == "reference_image" else binding["role"]}
            for binding in refs.get("asset_bindings", []) if binding["media_kind"] == "image"
        ] for key, refs in materials.items()}
        style_media_id = await project_style_media(session, project.id, plan.owner_id, "video")
        for references in material_references.values():
            if style_media_id and references and not any(item["media_id"] == style_media_id for item in references):
                references.append({"media_id": style_media_id, "role": "image"})
    preview = compile_preview(
        frozen,
        read_content_analysis(record),
        parent.result["assembly"],
        provider=provider,
        model=model,
        current_capability=current_capability,
        include_contract=True,
        material_references=material_references,
    )
    for compiled in preview["segments"]:
        compiled["asset_materials"] = materials.get(compiled["segment_key"], {})
    return parent, model, provider, preview


async def prepare_segment(session, plan, segment, parent, model, provider, compiled):
    from app.services.segment_material_preflight import require_segment_materials
    await require_segment_materials(session, plan, parent, compiled["segment_key"])
    from app.services.segment_asset_references import (
        material_guidance,
        material_snapshot,
        resolve_materials,
    )
    project = await session.get(Project, parent.project_id)
    resolved = await resolve_materials(session, project, segment.refs, segment=segment)
    if compiled["blockers"]:
        raise ConflictError("；".join(compiled["blockers"]))
    if compiled["input_contract"] is None or compiled["prompt"] is None:
        raise ConflictError("该模式尚未完成视频编译认证")
    if compiled["input_contract"]["protocol"] == "minimax_video_v2":
        raise ConflictError("该模式需专用媒体上传及请求审核，目前尚未完成冻结计划接线")
    contract = compiled["input_contract"]
    ids = list(dict.fromkeys(ref["media_id"] for ref in contract["effective_references"]))
    snapshot = await material_snapshot(session, resolved, ids)
    snapshots = {
        item["media"]["id"]: item["media"] for item in parent.payload["reference_snapshot"]
    }
    snapshots.update({item["id"]: item for item in snapshot["media"]})
    snapshot["media"] = [snapshots[media_id] for media_id in dict.fromkeys([*ids, *(item["id"] for item in snapshot["media"])])]
    total = 0
    for media_id in ids:
        if media_id not in snapshots:
            raise ConflictError("视频编译引入了未冻结的参考素材")
        total += await asyncio.to_thread(probe_image, snapshots[media_id], contract["protocol"])
    if total > 20 * 1024 * 1024:
        raise ConflictError("本片段参考图片总大小超过 20 MB")
    from app.services.audio_policy import for_episode
    from app.services.segment_voice_guidance_service import (
        append_voice_guidance,
        compile_segment_voice_guidance,
    )
    policy = await for_episode(session, project.id, segment.episode_id)
    policy["capability"] = compiled["audio"]
    voice_guidance = await compile_segment_voice_guidance(session, project, segment, resolved, policy=policy)
    from app.services.video_voice_reference_service import prepare, append_mapping
    voice_guidance = await prepare(session, project, resolved, voice_guidance, provider, model,
                                   {**contract, "effective_parameters": {**contract["effective_parameters"], "audio_policy": policy}})
    prompt = append_voice_guidance(compiled["prompt"], voice_guidance)
    prompt = append_mapping(prompt, voice_guidance)
    guidance = material_guidance({**resolved, "reference_media_ids": contract["reference_media_ids"]})
    if guidance:
        prompt += "\n\n" + guidance
    prompt, style = await frozen_video_style_prompt(session, parent.project_id, plan.owner_id, prompt)
    if style and style["media_id"] and contract["reference_media_ids"] and style["media_id"] not in contract["reference_media_ids"]:
        raise ConflictError("项目风格图未进入本次参考素材，请重新检查模型的图片数量限制")
    from app.services.video_input_compiler import prepare_video_prompt

    prompt, negative = prepare_video_prompt(provider, adapter_view(model), prompt, None)
    freeze = build_video_prompt_freeze(
        provider,
        model,
        script=compiled["structured_script"],
        input_contract=contract,
        active_prompt=prompt,
        style_snapshot=style,
        selection="model_compiler",
        asset_material_snapshot=snapshot,
        voice_reference=voice_guidance["audio_reference"],
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
        "asset_material_snapshot": snapshot,
        "asset_bindings": resolved["asset_bindings"],
        "sound_input": _sound_input_summary(
            resolved["asset_bindings"], {**contract["effective_parameters"], "audio_policy": policy}, voice_guidance),
    }


def validate_parameters(parameters, compiled):
    if set(parameters) - {"duration", "aspect_ratio", "resolution"} or any(
        parameters.get(key, compiled["parameters"][key]) != compiled["parameters"][key]
        for key in ("duration", "aspect_ratio", "resolution")
    ):
        raise ConflictError("生成设置与冻结计划不同，请在整集规划中检查模型规格")


async def build_plan(session, episode, plan, model_id, parameters, regenerate, business_executor, *, selected_segment_ids=None):
    if model_id != plan.provider_model_id:
        raise ConflictError("当前冻结计划属于其他模型，请先检查兼容性并切换规划")
    parent, model, provider, preview = await context(session, plan, selected_segment_ids=selected_segment_ids)
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(VideoSegment.plan_id == plan.id)
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    if selected_segment_ids is not None:
        if not selected_segment_ids or not set(selected_segment_ids).issubset({s.id for s in segments}):
            raise ConflictError("所选片段不属于当前制作稿，请重新选择")
        segments = [s for s in segments if s.id in selected_segment_ids]
    compiled_by_key = {item["segment_key"]: item for item in preview["segments"]}
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
    for segment in segments:
        compiled = compiled_by_key[segment.lineage_key]
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
            from app.services.segment_video_recovery_service import require_resolved_generation
            await require_resolved_generation(session, segment, episode.project_id, episode.owner_id)
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
                    "shot_id": shots[0].id,
                    "scene_id": shots[0].scene_id,
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
                "sound_input": prepared["sound_input"],
                "script_fingerprint": prepared["script_fingerprint"],
                "input_mode": prepared["contract"]["input_mode"],
                "protocol": prepared["contract"]["protocol"],
                "protocol_version": prepared["contract"]["protocol_version"],
                "first_frame_media_id": prepared["contract"]["first_frame_media_id"],
                "last_frame_media_id": prepared["contract"]["last_frame_media_id"],
                "reference_media_ids": prepared["contract"]["reference_media_ids"],
                "actions": prepared["contract"]["actions"],
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
    from app.services.planning_capability_admin import lock_catalog
    locked_model = await session.get(ProviderModel, provider_model_id)
    if locked_model is None:
        raise ConflictError("视频模型不存在")
    await lock_catalog(session, locked_model.provider_id, locked_model.id)
    from app.services.video_adapter_installation import require_installed
    await require_installed(session, locked_model, submitting=True)
    parent, model, provider, preview = await context(session, plan, selected_segment_ids=[segment.id])
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
            "sound_input": prepared["sound_input"],
            "content_planning_run_id": parent.id,
            "asset_bindings": prepared["asset_bindings"],
            "asset_material_snapshot": prepared["asset_material_snapshot"],
        },
    )
    from app.services.video_version_service import bind
    await bind(session, job, provider, model)
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
    if job.payload.get("video_execution_version"):
        from app.models import Provider
        from app.services.video_version_service import resolve
        model = await session.get(ProviderModel, job.payload["provider_model_id"])
        provider = await session.get(Provider, job.provider_id)
        provider, model = await resolve(session, job, provider, model)
        await context(session, plan, resolve_assets=False, execution=(provider, model))
    else:
        _, model, provider, _ = await context(session, plan, resolve_assets=False)
    from app.services.video_prompt_freeze_service import assert_frozen_video_prompt
    assert_frozen_video_prompt(job.payload, provider, model)
    content = await build_script_snapshot_content(session, segment)
    if fingerprint(content) != job.payload["script_fingerprint"]:
        raise ConflictError("视频来源在排队后发生变化，已停止收费提交")
    snapshot = job.payload.get("asset_material_snapshot")
    if not isinstance(snapshot, dict):
        raise ConflictError("视频任务缺少已锁定的素材版本")
    protocol = job.payload["video_input_contract"]["protocol"]
    from app.services.segment_asset_references import validate_material_snapshot
    await validate_material_snapshot(session, snapshot, protocol)
