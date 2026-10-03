"""Read-only validation and planning for episode video batches."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    Asset,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Project,
    SegmentVideoVersion,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.providers.protocols import is_toapis_model
from app.providers.video_contracts import VIDEO_CONTRACTS, validate_video_parameters
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing, _video_model_pair
from app.services.pricing_service import estimate as estimate_pricing
from app.services.team_access import owner_scope


async def build_episode_video_plan(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    provider_model_id: int,
    parameters: dict[str, Any],
    regenerate: bool,
) -> dict[str, Any]:
    """Validate an E5 segment batch without creating jobs or contacting a provider.

    The legacy ``*_shot_ids`` fields remain read-only aliases for clients created
    before E5.  They describe the shots contained by the selected segments; jobs
    are never created against those shot ids here.
    """
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
    from app.services import business_executor_service

    orchestrator_execution = await business_executor_service.resolve_execution(
        session, "media_task_orchestrator"
    )
    model, provider = await _video_model_pair(session, provider_model_id)
    from app.services import segment_plan_service

    if production is None or production.active_plan_id is None:
        await segment_plan_service.initialize_legacy_plan(session, episode)
        production = await session.scalar(
            select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
        )
    plan = (
        await session.get(EpisodeProductionPlan, production.active_plan_id)
        if production and production.active_plan_id
        else None
    )
    if plan is None:
        raise ConflictError("本集尚未建立片段生产计划")
    if plan.status not in {"confirmed", "compatibility"}:
        raise ConflictError("片段脚本尚未确认，请先保存并确认生产计划")
    if plan.source_script_revision != episode.script_revision:
        raise ConflictError("片段计划来源剧本已变化，请重新规划后再生产")
    project = await session.scalar(
        select(Project).where(Project.id == project_id, owner_scope(Project.owner_id, owner_id))
    )
    if project is None:
        raise NotFoundError("项目不存在")
    from app.services.style_generation_service import project_style_media
    style_media_id = await project_style_media(session, project.id, owner_id, "video")
    from app.services.asset_service import get_project_asset_readiness

    asset_readiness = await get_project_asset_readiness(session, project)
    episode_asset_state = next(
        (
            item
            for item in asset_readiness["episodes"]
            if item["episode_id"] == episode.id
        ),
        None,
    )
    asset_block_reason: str | None = None
    if episode_asset_state and episode_asset_state["status"] not in {
        "ready",
        "no_requirements",
    }:
        missing_names = [
            issue["asset_name"] for issue in episode_asset_state["issues"][:3]
        ]
        if episode_asset_state["status"] == "stale":
            asset_block_reason = "剧本版本变化后，角色或场景素材尚未复核"
        else:
            suffix = f"：{'、'.join(missing_names)}" if missing_names else ""
            asset_block_reason = f"缺少已确认的角色或场景最终图片{suffix}"
    if plan.provider_model_id is not None and plan.provider_model_id != model.id:
        raise ConflictError("当前片段计划使用了其他视频模型，请重新规划后再生产")
    if plan.status == "compatibility" and plan.provider_model_id is None:
        # Legacy one-shot-per-segment plans predate model snapshots. The first
        # explicitly confirmed production freezes the selected model instead of
        # applying the modern duration contract to historical shot-based plans.
        plan.provider_model_id = model.id
        plan.model_capability_snapshot = {
            **(plan.model_capability_snapshot or {}),
            "provider_model_id": model.id,
            "model_id": model.model_id,
            "legacy_compatibility": True,
        }
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(VideoSegment.plan_id == plan.id)
                .order_by(VideoSegment.order, VideoSegment.id)
            )
        ).all()
    )
    if not segments:
        raise ConflictError("当前片段计划为空，请重新规划")
    segment_ids = [segment.id for segment in segments]
    links = list(
        (
            await session.scalars(
                select(VideoSegmentShot)
                .where(VideoSegmentShot.segment_id.in_(segment_ids))
                .order_by(VideoSegmentShot.segment_id, VideoSegmentShot.order)
            )
        ).all()
    )
    links_by_segment: dict[int, list[VideoSegmentShot]] = {
        segment.id: [] for segment in segments
    }
    for link in links:
        links_by_segment[link.segment_id].append(link)
    shot_ids = [link.shot_id for link in links]
    shots = {
        item.id: item
        for item in (
            await session.scalars(select(Shot).where(Shot.id.in_(shot_ids)))
        ).all()
    }
    final_ids = set(
        (
            await session.scalars(
                select(SegmentVideoVersion.segment_id).where(
                    SegmentVideoVersion.segment_id.in_(segment_ids),
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
                    Job.target_id.in_(segment_ids),
                    Job.status.not_in(TERMINAL_STATUSES),
                )
            )
        ).all()
    )
    eligible: list[int] = []
    skipped: list[int] = []
    blocked: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    from app.providers.toapis import video_parameters
    video_contract: dict[str, Any] | None = None
    if plan.status == "confirmed":
        from app.services.video_model_contract import (
            normalize as normalize_video_model,
        )
        from app.services.video_model_contract import (
            validate_parameters,
            validate_segment,
        )

        video_contract = normalize_video_model(model)

    for segment in segments:
        segment_shots = [shots[link.shot_id] for link in links_by_segment[segment.id] if link.shot_id in shots]
        issue = {
            "segment_id": segment.id,
            "order": segment.order,
            "shot_ids": [item.id for item in segment_shots],
            # Compatibility aliases for pre-E5 clients.
            "shot_id": segment_shots[0].id if segment_shots else None,
            "scene_id": segment_shots[0].scene_id if segment_shots else None,
        }
        if segment.status == "archived":
            blocked.append({**issue, "reason": "片段已归档，请先恢复"})
            continue
        if segment.id in active_ids:
            blocked.append(
                {
                    **issue,
                    "reason": "该片段已有进行中的视频任务",
                }
            )
            continue
        if asset_block_reason:
            blocked.append({**issue, "reason": asset_block_reason})
            continue
        if segment.id in final_ids and not regenerate:
            skipped.append(segment.id)
            continue
        if any(shot.is_locked for shot in segment_shots):
            blocked.append(
                {
                    **issue,
                    "reason": "分镜已锁定，所在片段不能生成视频",
                }
            )
            continue
        prompt = (segment.prompt or "").strip()
        if not prompt:
            blocked.append(
                {
                    **issue,
                    "reason": "缺少片段供应商 Prompt",
                }
            )
            continue
        refs = segment.refs or {}
        blocking_unresolved = []
        for item in refs.get("unresolved_assets", []):
            if not isinstance(item, dict):
                blocking_unresolved.append(item)
                continue
            if item.get("asset_type") == "voice" or item.get("role") in {"first_frame", "last_frame"}:
                continue
            asset_id = item.get("asset_id")
            asset = await session.get(Asset, asset_id) if type(asset_id) is int else None
            if asset is not None and asset.project_id == project.id and (
                (asset.attributes or {}).get("derived_type") == "segment_first_frame"
            ):
                continue
            blocking_unresolved.append(item)
        if blocking_unresolved or refs.get("unmatched_assets"):
            blocked.append({**issue, "reason": "片段资产尚未匹配或未采用可用素材，请先完成引用核对"})
            continue
        try:
            from app.services.asset_binding_service import binding_role, resolve_asset_binding

            for binding in refs.get("asset_bindings", []):
                if not isinstance(binding, dict):
                    raise ValidationError("片段资产绑定格式无效")
                role = binding_role(binding)
                await resolve_asset_binding(
                    session,
                    project,
                    asset_id=int(binding["asset_id"]),
                    adoption_key=binding.get("adoption_key"),
                    asset_version_id=int(binding["asset_version_id"]),
                    media_file_id=binding.get("media_file_id"),
                    role=role,
                )
        except (KeyError, TypeError, ValueError, ConflictError, ValidationError) as exc:
            blocked.append({
                **issue,
                "reason": getattr(exc, "message", "片段资产绑定已失效，请重新选择"),
            })
            continue
        segment_parameters = {
            **(plan.parameters or {}),
            **(segment.parameters or {}),
            **parameters,
            "duration": segment.generation_duration,
        }
        project_ratio = (project.creation_settings or {}).get("aspect_ratio")
        if project_ratio and project_ratio not in {"default", "project"}:
            segment_parameters["aspect_ratio"] = project_ratio
        try:
            from app.services.job_video_batch_service import (
                _compile_segment_media,
                _resolve_continuity_input,
                _sound_input_summary,
            )
            from app.services.segment_voice_guidance_service import (
                append_voice_guidance,
                compile_segment_voice_guidance,
            )
            from app.services.video_input_compiler import compile_video_input, prepare_video_prompt

            first_frame, last_frame, reference_ids, _bindings = _compile_segment_media(
                refs, segment_parameters
            )
            continuity_frame, continuity_dependency = await _resolve_continuity_input(
                session, segment, refs
            )
            if continuity_frame is not None:
                if first_frame not in (None, continuity_frame):
                    raise ConflictError("连续帧依赖不能与手工首帧同时使用")
                first_frame = continuity_frame
            media_inputs = [
                *([{"media_id": first_frame, "role": "first_frame"}] if first_frame else []),
                *([{"media_id": last_frame, "role": "last_frame"}] if last_frame else []),
                *[{"media_id": media_id, "role": "reference_image"} for media_id in reference_ids],
            ]
            if style_media_id and not any(item["media_id"] == style_media_id for item in media_inputs):
                media_inputs.append({"media_id": style_media_id, "role": "reference_image",
                                     "purpose": "project_style"})
            voice_guidance = await compile_segment_voice_guidance(
                session, project, segment, refs
            )
            prompt = append_voice_guidance(prompt, voice_guidance)
            prompt, effective_negative_prompt = prepare_video_prompt(
                provider, model, prompt, segment.negative_prompt
            )
            compiled_input = compile_video_input(
                provider, model, media_inputs, segment_parameters,
                negative_prompt=effective_negative_prompt,
            )
            if not compiled_input["ready"]:
                reason = compiled_input["blockers"][0] if compiled_input["blockers"] else "视频输入需要逐项确认降级"
                raise ConflictError(reason)
            first_frame = compiled_input["first_frame_media_id"]
            last_frame = compiled_input["last_frame_media_id"]
            reference_ids = compiled_input["reference_media_ids"]
            segment_parameters = compiled_input["effective_parameters"]
            if video_contract is not None:
                validate_parameters(video_contract, segment_parameters)
                validate_segment(
                    video_contract,
                    generation_duration=segment.generation_duration,
                    shot_count=len(((segment.parameters or {}).get("structured_script") or {}).get("authored_shots") or segment_shots),
                    timeline_duration=segment.timeline_duration,
                    reference_count=len(reference_ids),
                )
            effective_parameters = {**(model.default_params or {}), **segment_parameters}
            from app.providers.protocols import effective_protocol
            if effective_protocol(provider, model) in VIDEO_CONTRACTS:
                validate_video_parameters(effective_protocol(provider, model), effective_parameters,
                                  negative_prompt=effective_negative_prompt,
                                  first=bool(effective_parameters.get("first_frame_media_id")),
                                  last=bool(effective_parameters.get("last_frame_media_id")),
                                  references=len(reference_ids))
            if is_toapis_model(provider, model):
                video_parameters(
                    model.model_id,
                    effective_parameters,
                    first=bool(first_frame),
                    last=bool(last_frame),
                    references=len(reference_ids),
                )
            from app.services.style_generation_service import frozen_video_style_prompt
            from app.services.video_prompt_freeze_service import (
                build_video_prompt_freeze,
                select_segment_video_prompt,
            )

            script = (segment.parameters or {}).get("structured_script")
            prompt, effective_negative_prompt, selection = select_segment_video_prompt(
                provider, model, script=script, input_contract=compiled_input,
                existing_prompt=prompt, existing_negative_prompt=effective_negative_prompt,
                source_negative_prompt=segment.negative_prompt,
                voice_guidance=voice_guidance,
            )
            prompt, style_snapshot = await frozen_video_style_prompt(
                session, project.id, owner_id, prompt
            )
            prompt_freeze = build_video_prompt_freeze(
                provider, model, script=script, input_contract=compiled_input,
                active_prompt=prompt, voice_guidance=voice_guidance,
                style_snapshot=style_snapshot, selection=selection,
            )
        except (ConflictError, ValidationError) as exc:
            blocked.append(
                {
                    **issue,
                    "reason": exc.message,
                }
            )
            continue
        quote = estimate_pricing(model, prompt, effective_parameters)
        quotes.append({
            "segment_id": segment.id,
            "order": segment.order,
            "generation_duration": segment.generation_duration,
            "shot_ids": issue["shot_ids"],
            "video_input": {
                "fingerprint": compiled_input["fingerprint"],
                "protocol": compiled_input["protocol"],
                "protocol_version": compiled_input["protocol_version"],
                "input_mode": compiled_input["input_mode"],
                "first_frame_media_id": compiled_input["first_frame_media_id"],
                "last_frame_media_id": compiled_input["last_frame_media_id"],
                "reference_media_ids": compiled_input["reference_media_ids"],
                "actions": compiled_input["actions"],
                "continuity_dependency": continuity_dependency,
                "sound_input": _sound_input_summary(
                    _bindings, segment_parameters, voice_guidance
                ),
                "video_prompt_freeze": prompt_freeze,
            },
            **quote,
        })
        eligible.append(segment.id)
    pricing = _aggregate_video_pricing(quotes)
    eligible_shots = [
        link.shot_id for link in links if link.segment_id in eligible
    ]
    skipped_shots = [
        link.shot_id for link in links if link.segment_id in skipped
    ]
    return {
        "project_id": project_id,
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
        "eligible_shot_ids": eligible_shots,
        "skipped_final_shot_ids": skipped_shots,
        "blocked": blocked,
        "total_segments": len(segments),
        "total_shots": len(shots),
        "total_generation_duration": sum(
            segment.generation_duration for segment in segments if segment.id in eligible
        ),
        "estimated_count": len(quotes),
        "pricing_estimate": pricing,
        "video_inputs": [
            {
                "segment_id": item["segment_id"],
                **item["video_input"],
            }
            for item in quotes
        ],
        "requires_confirmation": True,
        "business_executor": orchestrator_execution,
    }
