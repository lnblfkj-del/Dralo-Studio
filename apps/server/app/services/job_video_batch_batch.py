"""Batch video planning, idempotent attempts and project submissions."""

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_TYPE_VIDEO,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Scene,
    Shot,
    VideoSegment,
    VideoSegmentShot,
)
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing
from app.services.job_video_batch_helpers import _enforce_cost_limit
from app.services.job_video_batch_single import (
    create_segment_video_job,
    create_shot_video_job,
)
from app.services.job_video_plan_service import build_episode_video_plan
from app.services.team_access import owner_scope


async def _lock_attempt_scope(session, owner_id, project_id, episode_id):
    active_plan = await session.scalar(select(EpisodeProductionPlan).join(
        EpisodeProduction, EpisodeProduction.active_plan_id == EpisodeProductionPlan.id
    ).join(Episode, Episode.id == EpisodeProduction.episode_id).where(
        Episode.id == episode_id, Episode.project_id == project_id, owner_scope(Episode.owner_id, owner_id)
    ))
    if active_plan and active_plan.source_type == "content_frozen":
        from app.services import episode_planning_workflow as workflow

        await workflow._lock_parent(session, active_plan.parameters["planning_run_id"])
    await session.execute(update(Episode).where(
        Episode.id == episode_id, Episode.project_id == project_id, owner_scope(Episode.owner_id, owner_id)
    ).values(number=Episode.number))


async def create_episode_segment_jobs(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    provider_model_id: int,
    segment_ids: list[int],
    parameters: dict[str, Any],
    request_id: str,
    regenerate: bool,
    plan_summary: dict[str, Any],
) -> tuple[Job, list[Job]]:
    """Create the E5 episode parent and one immutable child per segment."""
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(
                    VideoSegment.id.in_(segment_ids),
                    VideoSegment.episode_id == episode_id,
                )
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    if len(segments) != len(segment_ids):
        raise ConflictError("片段计划已变化，请重新预检")
    plan = await session.get(EpisodeProductionPlan, plan_summary["plan_id"])
    if plan is None or plan.episode_id != episode_id:
        raise ConflictError("片段计划已变化，请重新预检")
    links = list(
        (
            await session.scalars(
                select(VideoSegmentShot)
                .where(VideoSegmentShot.segment_id.in_(segment_ids))
                .order_by(VideoSegmentShot.segment_id, VideoSegmentShot.order)
            )
        ).all()
    )
    shots_by_segment: dict[int, list[int]] = {segment.id: [] for segment in segments}
    for link in links:
        shots_by_segment[link.segment_id].append(link.shot_id)
    parent = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type=JOB_TYPE_VIDEO,
        status=JOB_STATUS_PROCESSING,
        progress=0,
        # Keep the established parent discriminator so historical clients still
        # discover the operation; payload.production_unit is authoritative in E5.
        target_type="episode_video_batch",
        target_id=episode_id,
        payload={
            "production_unit": "video_segment",
            "plan_id": plan_summary["plan_id"],
            "plan_version": plan_summary["plan_version"],
            "plan_revision": plan_summary["plan_revision"],
            "segment_ids": segment_ids,
            "episode_id": episode_id,
            "episode_number": plan_summary["episode_number"],
            "request_id": request_id,
            "provider_model_id": provider_model_id,
            "parameters": parameters,
            "regenerate": regenerate,
            "pricing_snapshot": plan_summary["pricing_estimate"],
            "total_generation_duration": plan_summary["total_generation_duration"],
            "business_executor": plan_summary["business_executor"],
        },
        result={
            "production_unit": "video_segment",
            "total": len(segments),
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "child_job_ids": [],
        },
        cost_estimate=plan_summary["pricing_estimate"].get("estimated_cents"),
    )
    session.add(parent)
    await session.flush()
    children: list[Job] = []
    planned_inputs = {item["segment_id"]: item for item in plan_summary.get("video_inputs") or []}
    planned_quotes = {
        item["segment_id"]: item
        for item in (plan_summary.get("pricing_estimate") or {}).get("breakdown") or []
    }
    for segment in segments:
        child = await create_segment_video_job(
            session,
            owner_id,
            segment=segment,
            project_id=project_id,
            provider_model_id=provider_model_id,
            parameters=parameters,
            plan_parameters=plan.parameters or {},
            shot_ids=shots_by_segment[segment.id],
        )
        planned = planned_inputs.get(segment.id) or {}
        actual = child.payload or {}
        if (planned.get("fingerprint") != (actual.get("video_input_contract") or {}).get("fingerprint")
                or planned.get("continuity_dependency") != actual.get("continuity_dependency")
                or planned.get("sound_input") != actual.get("sound_input")
                or (planned.get("video_prompt_freeze") or {}).get("fingerprint")
                != (actual.get("video_prompt_freeze") or {}).get("fingerprint")):
            raise ConflictError("批量片段的素材、声线或提示词已变化，请重新预检")
        if (planned_quotes.get(segment.id) or {}).get("estimated_cents") != child.cost_estimate:
            raise ConflictError("批量片段实际报价与预检不一致，请重新预检")
        child.parent_job_id = parent.id
        child.payload = {
            **child.payload,
            "business_executor": plan_summary["business_executor"],
        }
        child.payload = {
            **child.payload,
            "episode_number": plan_summary["episode_number"],
        }
        children.append(child)
    parent.result = {
        **parent.result,
        "child_job_ids": [child.id for child in children],
    }
    await session.flush()
    return parent, children


async def build_segment_video_plan(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    segment_id: int,
    provider_model_id: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Build a paid-operation preview for exactly one active segment."""
    episode_plan = await build_episode_video_plan(
        session,
        owner_id,
        project_id=project_id,
        episode_id=episode_id,
        provider_model_id=provider_model_id,
        parameters=parameters,
        regenerate=True,
    )
    target_blocked = [
        item for item in episode_plan["blocked"] if item.get("segment_id") == segment_id
    ]
    target_eligible = [
        item for item in episode_plan["eligible_segment_ids"] if item == segment_id
    ]
    if not target_blocked and not target_eligible:
        raise NotFoundError("当前活动计划中不存在该视频片段")
    quotes = [
        item
        for item in episode_plan["pricing_estimate"].get("breakdown", [])
        if item.get("segment_id") == segment_id
    ]
    issue = target_blocked[0] if target_blocked else quotes[0]
    shot_ids = list(issue.get("shot_ids") or [])
    return {
        **episode_plan,
        "eligible_segment_ids": target_eligible,
        "skipped_final_segment_ids": [],
        "eligible_shot_ids": shot_ids if target_eligible else [],
        "skipped_final_shot_ids": [],
        "blocked": target_blocked,
        "total_segments": 1,
        "total_shots": len(shot_ids),
        "total_generation_duration": sum(
            float(item.get("generation_duration") or 0) for item in quotes
        ),
        "estimated_count": len(quotes),
        "pricing_estimate": _aggregate_video_pricing(quotes),
        "video_inputs": [
            item
            for item in episode_plan.get("video_inputs", [])
            if item.get("segment_id") == segment_id
        ],
    }


async def start_segment_video_attempt(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    segment_id: int,
    provider_model_id: int,
    parameters: dict[str, Any],
    request_id: str,
    max_cost_cents: int,
    expected_plan_id: int,
    expected_plan_revision: int,
    expected_video_prompt_fingerprint: str | None = None,
) -> Job:
    """Create one idempotent candidate attempt without touching other segments."""
    await _lock_attempt_scope(session, owner_id, project_id, episode_id)
    request_fingerprint = sha256(
        json.dumps(
            {
                "project_id": project_id,
                "episode_id": episode_id,
                "segment_id": segment_id,
                "provider_model_id": provider_model_id,
                "parameters": parameters,
                "max_cost_cents": max_cost_cents,
                "expected_plan_id": expected_plan_id,
                "expected_plan_revision": expected_plan_revision,
                "expected_video_prompt_fingerprint": expected_video_prompt_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing_jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == "video_segment",
                    Job.target_id == segment_id,
                )
                .order_by(Job.id.desc())
            )
        ).all()
    )
    for existing in existing_jobs:
        if (existing.payload or {}).get("request_id") != request_id:
            continue
        if (existing.payload or {}).get("request_fingerprint") != request_fingerprint:
            raise ConflictError("相同请求编号的片段生成参数不一致，请刷新任务状态")
        return existing

    plan_summary = await build_segment_video_plan(
        session,
        owner_id,
        project_id=project_id,
        episode_id=episode_id,
        segment_id=segment_id,
        provider_model_id=provider_model_id,
        parameters=parameters,
    )
    if plan_summary["plan_id"] != expected_plan_id:
        raise ConflictError("片段计划已变化，请重新预检费用")
    if plan_summary["plan_revision"] != expected_plan_revision:
        raise ConflictError("片段计划内容已变化，请重新预检费用")
    if plan_summary["blocked"]:
        raise ConflictError(plan_summary["blocked"][0]["reason"])
    if plan_summary["eligible_segment_ids"] != [segment_id]:
        raise ConflictError("当前片段不能创建视频候选")
    if expected_video_prompt_fingerprint is not None:
        current_prompt = next(iter(plan_summary.get("video_inputs") or []), {}).get("video_prompt_freeze") or {}
        if current_prompt.get("fingerprint") != expected_video_prompt_fingerprint:
            raise ConflictError("片段提示词或项目风格已变化，请重新预检费用")
    _enforce_cost_limit(
        plan_summary["pricing_estimate"], max_cost_cents, label="当前片段"
    )

    segment = await session.scalar(
        select(VideoSegment).where(
            VideoSegment.id == segment_id,
            VideoSegment.episode_id == episode_id,
            VideoSegment.plan_id == expected_plan_id,
        )
    )
    if segment is None:
        raise ConflictError("片段计划已变化，请重新预检费用")
    shot_ids = list(
        (
            await session.scalars(
                select(VideoSegmentShot.shot_id)
                .where(VideoSegmentShot.segment_id == segment_id)
                .order_by(VideoSegmentShot.order)
            )
        ).all()
    )
    plan = await session.get(EpisodeProductionPlan, expected_plan_id)
    if plan is None:
        raise ConflictError("片段计划已变化，请重新预检费用")
    job = await create_segment_video_job(
        session,
        owner_id,
        segment=segment,
        project_id=project_id,
        provider_model_id=provider_model_id,
        parameters=parameters,
        plan_parameters=plan.parameters or {},
        shot_ids=shot_ids,
    )
    planned_input = next(iter(plan_summary.get("video_inputs") or []), None)
    actual_contract = (job.payload or {}).get("video_input_contract") or {}
    if (
        not planned_input
        or planned_input.get("fingerprint") != actual_contract.get("fingerprint")
    ):
        raise ConflictError("片段视频输入在确认后发生变化，请重新预检")
    if planned_input.get("continuity_dependency") != (
        (job.payload or {}).get("continuity_dependency")
    ):
        raise ConflictError("前序片段采用版本已变化，请重新预检")
    if planned_input.get("sound_input") != (job.payload or {}).get("sound_input"):
        raise ConflictError("角色声线或声音资产已变化，请重新预检")
    planned_prompt = (planned_input or {}).get("video_prompt_freeze") or {}
    actual_prompt = (job.payload or {}).get("video_prompt_freeze") or {}
    if planned_prompt.get("fingerprint") != actual_prompt.get("fingerprint"):
        raise ConflictError("片段提示词、脚本或模型档案已变化，请重新预检")
    job.payload = {
        **job.payload,
        "request_id": request_id,
        "request_fingerprint": request_fingerprint,
        "confirmed_max_cost_cents": max_cost_cents,
        "operation_scope": "single_segment",
        "business_executor": plan_summary["business_executor"],
        "episode_number": plan_summary["episode_number"],
        "pricing_preview": plan_summary["pricing_estimate"],
    }
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode_id)
    )
    if production is not None:
        production.last_error = None
    await session.flush()
    return job


async def create_batch_video_jobs(
    session: AsyncSession, owner_id: int, *, provider_model_id: int,
    shot_ids: list[int], parameters: dict[str, Any], project_id: int,
    episode_id: int | None = None, request_id: str | None = None,
    regenerate: bool = False,
) -> tuple[Job, list[Job]]:
    ids = list(dict.fromkeys(shot_ids))
    shots = list((await session.execute(
        select(Shot).where(Shot.id.in_(ids), owner_scope(Shot.owner_id, owner_id))
    )).scalars())
    if len(shots) != len(ids):
        raise NotFoundError("批量分镜中包含不存在的分镜")
    project_ids = list((await session.execute(
        select(Episode.project_id).join(Scene, Scene.episode_id == Episode.id).join(Shot, Shot.scene_id == Scene.id)
        .where(Shot.id.in_(ids))
    )).scalars())
    if len(set(project_ids)) != 1:
        raise ConflictError("批量视频分镜必须属于同一项目")
    if project_ids[0] != project_id:
        raise NotFoundError("项目不存在")
    parent_target = "episode_video_batch" if episode_id is not None else "video_batch"
    parent = Job(owner_id=owner_id, project_id=project_ids[0], job_type=JOB_TYPE_VIDEO,
                 status=JOB_STATUS_PROCESSING, progress=0, target_type=parent_target,
                 target_id=episode_id,
                 payload={"shot_ids": ids, "episode_id": episode_id, "request_id": request_id,
                          "provider_model_id": provider_model_id, "parameters": parameters,
                          "regenerate": regenerate},
                 result={"total": len(ids), "completed": 0, "succeeded": 0,
                         "failed": 0, "cancelled": 0, "child_job_ids": []})
    session.add(parent)
    await session.flush()
    children = []
    for shot in shots:
        shot_parameters = dict(parameters)
        shot_parameters.setdefault("duration", round(float(shot.duration or 4)))
        child = await create_shot_video_job(
            session, owner_id, shot_id=shot.id, provider_model_id=provider_model_id,
            prompt=shot.prompt or shot.action or "镜头视频", negative_prompt=shot.negative_prompt,
            first_frame_media_id=None, last_frame_media_id=None, reference_media_ids=[],
            parameters=shot_parameters,
        )
        child.parent_job_id = parent.id
        children.append(child)
    quotes = [child.payload.get("pricing_snapshot", {}) for child in children]
    pricing = _aggregate_video_pricing(quotes)
    parent.payload = {**parent.payload, "pricing_snapshot": pricing}
    parent.cost_estimate = pricing.get("estimated_cents")
    parent.result = {
        "total": len(children),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
        "child_job_ids": [child.id for child in children],
    }
    await session.flush()
    return parent, children


async def start_episode_video_batch(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    provider_model_id: int,
    parameters: dict[str, Any],
    regenerate: bool,
    request_id: str,
    max_cost_cents: int,
    expected_plan_id: int | None = None,
    expected_plan_revision: int | None = None,
) -> Job:
    await _lock_attempt_scope(session, owner_id, project_id, episode_id)
    request_fingerprint = sha256(
        json.dumps(
            {
                "project_id": project_id,
                "episode_id": episode_id,
                "provider_model_id": provider_model_id,
                "parameters": parameters,
                "regenerate": regenerate,
                "max_cost_cents": max_cost_cents,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing_jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == "episode_video_batch",
                    Job.target_id == episode_id,
                )
                .order_by(Job.id.desc())
            )
        ).all()
    )
    for existing in existing_jobs:
        if (existing.payload or {}).get("request_id") == request_id:
            previous_fingerprint = (existing.payload or {}).get("request_fingerprint")
            if previous_fingerprint and previous_fingerprint != request_fingerprint:
                raise ConflictError("相同请求编号的生产参数不一致，请刷新任务状态")
            return existing
    if any(existing.status not in TERMINAL_STATUSES for existing in existing_jobs):
        raise ConflictError("本集已有进行中的批量生产任务")
    plan = await build_episode_video_plan(
        session,
        owner_id,
        project_id=project_id,
        episode_id=episode_id,
        provider_model_id=provider_model_id,
        parameters=parameters,
        regenerate=regenerate,
    )
    if expected_plan_id is not None and plan["plan_id"] != expected_plan_id:
        raise ConflictError("片段计划已变化，请重新预检费用")
    if (
        expected_plan_revision is not None
        and plan["plan_revision"] != expected_plan_revision
    ):
        raise ConflictError("片段计划内容已变化，请重新预检费用")
    if plan["blocked"]:
        first = plan["blocked"][0]
        raise ConflictError(
            f"片段 {first['order']} 无法批量生产：{first['reason']}"
        )
    if not plan["eligible_segment_ids"]:
        if plan["skipped_final_segment_ids"]:
            raise ConflictError("本集片段均已有最终视频版本；如需重做，请开启重新生成")
        raise ConflictError("本集没有可生成的视频片段")
    _enforce_cost_limit(plan["pricing_estimate"], max_cost_cents, label="本集片段")
    parent, _children = await create_episode_segment_jobs(
        session,
        owner_id,
        project_id=project_id,
        episode_id=episode_id,
        provider_model_id=provider_model_id,
        segment_ids=plan["eligible_segment_ids"],
        parameters=parameters,
        request_id=request_id,
        regenerate=regenerate,
        plan_summary=plan,
    )
    parent.payload = {**parent.payload, "plan": plan}
    parent.payload = {
        **parent.payload,
        "request_fingerprint": request_fingerprint,
        "confirmed_max_cost_cents": max_cost_cents,
    }
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode_id)
    )
    if production is not None:
        production.last_error = None
    await session.flush()
    return parent


async def start_project_episode_batches(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_ids: list[int],
    provider_model_id: int,
    parameters: dict[str, Any],
    regenerate: bool,
    request_id: str,
    max_cost_cents: int,
) -> list[Job]:
    existing = list(
        (
            await session.scalars(
                select(Job).where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == "episode_video_batch",
                    Job.target_id.in_(episode_ids),
                )
            )
        ).all()
    )
    existing_by_episode = {
        item.target_id: item
        for item in existing
        if (item.payload or {}).get("request_id")
        == f"{request_id}:{item.target_id}"
    }
    if len(existing_by_episode) == len(episode_ids):
        return [existing_by_episode[episode_id] for episode_id in episode_ids]
    if existing_by_episode:
        raise ConflictError(
            "本次跨集请求只创建了部分任务，请刷新任务状态，不要更换请求编号重发"
        )
    episodes = list(
        (
            await session.scalars(
                select(Episode).where(
                    Episode.id.in_(episode_ids),
                    Episode.project_id == project_id,
                    Episode.status != "archived",
                    owner_scope(Episode.owner_id, owner_id),
                )
            )
        ).all()
    )
    if len(episodes) != len(episode_ids):
        raise NotFoundError("批量列表中包含不存在的分集")
    episode_numbers = {item.id: item.number for item in episodes}
    # Validate every episode before creating the first job, so a bad episode
    # cannot leave a partially submitted project batch.
    plans: dict[int, dict[str, Any]] = {}
    for episode_id in episode_ids:
        plan = await build_episode_video_plan(
            session,
            owner_id,
            project_id=project_id,
            episode_id=episode_id,
            provider_model_id=provider_model_id,
            parameters=parameters,
            regenerate=regenerate,
        )
        if plan["blocked"] or not plan["eligible_segment_ids"]:
            reason = (
                plan["blocked"][0]["reason"]
                if plan["blocked"]
                else "没有可生成的片段视频"
            )
            raise ConflictError(f"第 {episode_numbers[episode_id]} 集：{reason}")
        plans[episode_id] = plan
    total_estimated = sum(
        _enforce_cost_limit(
            plans[episode_id]["pricing_estimate"],
            max_cost_cents,
            label=f"第 {episode_numbers[episode_id]} 集",
        )
        for episode_id in episode_ids
    )
    _enforce_cost_limit(
        {"estimated_cents": total_estimated}, max_cost_cents, label="所选分集"
    )
    jobs = []
    for episode_id in episode_ids:
        jobs.append(
            await start_episode_video_batch(
                session,
                owner_id,
                project_id=project_id,
                episode_id=episode_id,
                provider_model_id=provider_model_id,
                parameters=parameters,
                regenerate=regenerate,
                request_id=f"{request_id}:{episode_id}",
                max_cost_cents=plans[episode_id]["pricing_estimate"][
                    "estimated_cents"
                ],
            )
        )
    return jobs
