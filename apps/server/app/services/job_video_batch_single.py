"""Single-shot and single-segment video job creation."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Episode, EpisodeProductionPlan, Job, Project, Scene, Shot, VideoSegment
from app.services import job_creation_service as _job_creation_service
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_video_batch_helpers import (
    _compile_segment_media,
    _resolve_continuity_input,
    _sound_input_summary,
)
from app.services.team_access import owner_scope


async def create_shot_video_job(
    session: AsyncSession, owner_id: int, *, shot_id: int, **data: Any
) -> Job:
    shot = await session.scalar(
        select(Shot)
        .where(Shot.id == shot_id, owner_scope(Shot.owner_id, owner_id))
        .with_for_update()
    )
    if shot is None:
        raise NotFoundError("分镜不存在")
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    if shot.status == SHOT_STATUS_SUPERSEDED:
        raise ConflictError("历史镜头不能创建新视频任务，请选择当前计划的镜头")
    if shot.is_locked:
        raise ConflictError("分镜已锁定，不能生成新视频版本")
    from app.models import EpisodeProduction, VideoSegmentShot

    frozen = await session.scalar(select(EpisodeProductionPlan.id).join(
        VideoSegment, VideoSegment.plan_id == EpisodeProductionPlan.id
    ).join(VideoSegmentShot, VideoSegmentShot.segment_id == VideoSegment.id).join(
        EpisodeProduction, EpisodeProduction.active_plan_id == EpisodeProductionPlan.id
    ).where(VideoSegmentShot.shot_id == shot.id, EpisodeProductionPlan.source_type == "content_frozen"))
    if frozen:
        raise ConflictError("镜头已属于冻结片段，请通过片段预检生成视频，不能单独改变提交数量")
    active = await session.scalar(
        select(Job.id).where(
            Job.target_type == "shot",
            Job.target_id == shot.id,
            Job.status.not_in(TERMINAL_STATUSES),
        )
    )
    if active is not None:
        raise ConflictError("该分镜已有进行中的视频任务，请等待完成后再试")
    project_id = await session.scalar(
        select(Episode.project_id).join(Scene, Scene.episode_id == Episode.id).where(Scene.id == shot.scene_id)
    )
    job = await _job_creation_service.create_video_job(session, owner_id, project_id=project_id, **data)
    job.target_type = "shot"
    job.target_id = shot.id
    shot.status = "generating"
    await session.flush()
    return job


async def create_segment_video_job(
    session: AsyncSession,
    owner_id: int,
    *,
    segment: VideoSegment,
    project_id: int,
    provider_model_id: int,
    parameters: dict[str, Any],
    plan_parameters: dict[str, Any],
    shot_ids: list[int],
) -> Job:
    """Create one paid-capable video job for exactly one VideoSegment."""
    if segment.status == "archived":
        raise ConflictError("片段已归档，请先恢复")
    if segment.status == "generating":
        raise ConflictError("该片段已有进行中的视频任务")
    active = await session.scalar(
        select(Job.id).where(
            Job.target_type == "video_segment",
            Job.target_id == segment.id,
            Job.status.not_in(TERMINAL_STATUSES),
        )
    )
    if active is not None:
        raise ConflictError("该片段已有进行中的视频任务")
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    if plan and plan.source_type == "content_frozen":
        from app.services.episode_planning_video_production import create_job

        return await create_job(session, owner_id, segment, project_id=project_id,
                                provider_model_id=provider_model_id, parameters=parameters, shot_ids=shot_ids)
    from app.services.production_snapshot_service import capture_script
    script_snapshot = await capture_script(session, segment)
    refs = script_snapshot.content.get("refs") or {}
    effective = {
        **plan_parameters,
        **(segment.parameters or {}),
        **parameters,
        "duration": segment.generation_duration,
    }
    first_frame_media_id, last_frame_media_id, reference_media_ids, bindings = (
        _compile_segment_media(refs, effective)
    )
    continuity_frame_id, continuity_dependency = await _resolve_continuity_input(
        session, segment, refs
    )
    if continuity_frame_id is not None:
        if first_frame_media_id not in (None, continuity_frame_id):
            raise ConflictError("连续帧依赖不能与手工首帧同时使用")
        first_frame_media_id = continuity_frame_id
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("项目不存在")
    project_ratio = (project.creation_settings or {}).get("aspect_ratio")
    if project_ratio and project_ratio not in {"default", "project"}:
        effective["aspect_ratio"] = project_ratio
    from app.services.audio_policy import apply_prompt, video_context
    from app.services.job_pricing_service import _video_model_pair
    from app.services.segment_voice_guidance_service import (
        append_voice_guidance,
        compile_segment_voice_guidance,
    )
    model, provider = await _video_model_pair(session, provider_model_id)
    effective = await video_context(session, project, segment, provider, model, effective)
    voice_guidance = await compile_segment_voice_guidance(
        session, project, segment, refs, policy=effective["audio_policy"]
    )
    base_prompt, omitted = apply_prompt(segment.prompt, (segment.parameters or {}).get("structured_script"), effective["audio_policy"])
    effective_prompt = append_voice_guidance(base_prompt, voice_guidance)
    job = await _job_creation_service.create_video_job(
        session,
        owner_id,
        provider_model_id=provider_model_id,
        prompt=effective_prompt,
        project_id=project_id,
        negative_prompt=segment.negative_prompt,
        first_frame_media_id=first_frame_media_id,
        last_frame_media_id=last_frame_media_id,
        reference_media_ids=reference_media_ids,
        parameters=effective,
    )
    from app.services.job_pricing_service import _video_model_pair
    from app.services.pricing_service import attach as attach_pricing
    from app.services.style_generation_service import frozen_video_style_prompt
    from app.services.video_prompt_freeze_service import (
        build_video_prompt_freeze,
        select_segment_video_prompt,
    )

    model, provider = await _video_model_pair(session, provider_model_id)
    frozen_input = {
        **job.payload["video_input_contract"],
        "ready": True,
        "blockers": [],
        "required_confirmations": [],
        "effective_references": job.payload["video_input_effective_references"],
        "effective_parameters": job.payload["parameters"],
    }
    script = (script_snapshot.content.get("parameters") or {}).get("structured_script")
    prompt, negative, selection = select_segment_video_prompt(
        provider, model, script=script, input_contract=frozen_input,
        existing_prompt=job.payload["prompt"],
        existing_negative_prompt=job.payload.get("negative_prompt"),
        source_negative_prompt=segment.negative_prompt, voice_guidance=voice_guidance,
    )
    styled_prompt, style_snapshot = await frozen_video_style_prompt(
        session, project_id, owner_id, prompt
    )
    job.payload = {
        **job.payload, "prompt": styled_prompt, "negative_prompt": negative,
        "style_snapshot": style_snapshot,
    }
    attach_pricing(job, model)
    prompt_freeze = build_video_prompt_freeze(
        provider, model, script=script, input_contract=frozen_input,
        active_prompt=job.payload["prompt"], voice_guidance=voice_guidance,
        style_snapshot=style_snapshot, selection=selection,
    )
    job.target_type = "video_segment"
    job.target_id = segment.id
    job.payload = {
        **job.payload,
        "asset_bindings": bindings,
        "script_snapshot_id": script_snapshot.id,
        "script_fingerprint": script_snapshot.fingerprint,
        "script_snapshot": script_snapshot.content,
        "plan_id": segment.plan_id,
        "episode_id": segment.episode_id,
        "segment_order": segment.order,
        "shot_ids": shot_ids,
        "generation_duration": segment.generation_duration,
        "continuity_dependency": continuity_dependency,
        "sound_input": _sound_input_summary(bindings, effective, voice_guidance),
        "video_prompt_freeze": prompt_freeze,
        "audio_policy": effective["audio_policy"],
        "omitted_audio_sources": omitted,
    }
    from app.services.segment_video_candidate_service import attach_input_evidence

    attach_input_evidence(job)
    segment.status = "generating"
    await session.flush()
    return job


