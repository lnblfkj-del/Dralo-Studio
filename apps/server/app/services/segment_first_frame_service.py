"""Create optional segment first-frame assets without a separate workspace."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_TYPE_IMAGE,
    Asset,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Project,
    Provider,
    ProviderModel,
    VideoSegment,
)
from app.providers.protocols import validate_model_protocol
from app.services import asset_service, job_service
from app.services.asset_binding_service import binding_role, resolve_asset_binding
from app.services.job_pricing_service import _aggregate_video_pricing
from app.services.pricing_service import estimate as estimate_pricing

TARGET_SEGMENT_FIRST_FRAME_BATCH = "segment_first_frame_batch"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _frame_slug(episode_id: int, lineage_key: str) -> str:
    return f"ep{episode_id}-segment-{lineage_key[:24]}-first-frame"


def _opening_frame_description(segment: VideoSegment) -> str:
    script = (segment.parameters or {}).get("structured_script") or {}
    document = script.get("editor_document") if isinstance(script, dict) else None
    if not isinstance(document, dict):
        if not isinstance(script, dict) or not script.get("camera"):
            return segment.prompt
        scene = script.get("scene") or {}
        camera = script["camera"][0]
        shot_id = camera.get("shot_id")
        performance = next((item for item in script.get("performances") or []
                            if item.get("shot_id") == shot_id), {})
        parts = [
            " · ".join(str(scene.get(key) or "") for key in ("name", "location", "time_of_day") if scene.get(key)),
            str(scene.get("description") or ""),
            str(script.get("entry_state") or ""),
            " · ".join(str(camera.get(key) or "") for key in ("shot_size", "camera_angle") if camera.get(key)),
            "，".join(str(performance.get(key) or "") for key in ("subject", "expression", "action") if performance.get(key)).split("；", 1)[0],
        ]
        return "\n".join(part for part in parts if part) or segment.prompt
    opening: list[str] = []
    in_first_shot = False
    shot_lines = 0
    for paragraph in document.get("content") or []:
        if not isinstance(paragraph, dict):
            continue
        nodes = paragraph.get("content") or []
        if any(isinstance(node, dict) and node.get("type") == "scriptTool"
               and (node.get("attrs") or {}).get("kind") == "shot" for node in nodes):
            if in_first_shot:
                break
            in_first_shot = True
        if in_first_shot and shot_lines >= 2:
            break
        parts = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kind = node.get("type")
            attrs = node.get("attrs") or {}
            if kind == "text":
                parts.append(str(node.get("text") or ""))
            elif kind == "assetMention":
                parts.append(str(attrs.get("name") or ""))
            elif kind == "scriptTool" and attrs.get("kind") == "shot":
                parts.append(f"首镜头，{attrs.get('duration', '')}秒")
        line = "".join(parts).strip()
        if in_first_shot and line.startswith(("环境声", "音效", "配乐")):
            break
        if not line:
            continue
        if in_first_shot and shot_lines == 1:
            line = line.split("；", 1)[0].split("，镜头", 1)[0]
        opening.append(line)
        if in_first_shot:
            shot_lines += 1
    return "\n".join(opening) if in_first_shot and opening else segment.prompt


def _frame_prompt(segment: VideoSegment) -> str:
    return (
        "生成该视频片段的首帧定格图。严格保持所引用角色、造型、道具和场景的身份与外观一致，"
        "但参考图只用于身份和外观，不得覆盖片段指定的构图与空间位置。"
        "按脚本准确呈现主体、场景和道具的前后、上下及遮挡关系，不添加脚本未提及的天气或物体。"
        "只呈现首镜头开始时的静止状态，不呈现后续动作、镜头终点或结束状态。"
        "不要新增人物，不要添加文字、水印或分镜排版。画面必须能作为后续图生视频的第一帧。\n\n"
        f"片段标题：{segment.title or f'片段 {segment.order:02d}'}\n"
        f"首帧依据：{_opening_frame_description(segment)}"
    )


async def _frame_asset(
    session: AsyncSession, project: Project, episode: Episode, segment: VideoSegment
) -> Asset:
    slug = _frame_slug(episode.id, segment.lineage_key)
    existing = await session.scalar(
        select(Asset).where(Asset.project_id == project.id, Asset.slug == slug)
    )
    if existing is not None:
        if (existing.attributes or {}).get("derived_type") != "segment_first_frame":
            raise ConflictError(f"引用名称 @{slug} 已被其他资产使用")
        return existing
    return await asset_service.create_asset(
        session,
        project,
        {
            "asset_type": "reference",
            "name": f"第{episode.number}集片段{segment.order:02d}首帧",
            "slug": slug,
            "description": "分集视频片段的可选首帧资产。",
            "prompt_anchor": _frame_prompt(segment),
            "attributes": {
                "derived_type": "segment_first_frame",
                "episode_id": episode.id,
                "segment_id": segment.id,
                "segment_lineage_key": segment.lineage_key,
            },
        },
    )


def _reference_media(segment: VideoSegment) -> list[int]:
    refs = segment.refs or {}
    bindings = refs.get("asset_bindings") or []
    frame_media = {
        item.get("media_file_id")
        for item in bindings
        if isinstance(item, dict) and binding_role(item) in {"first_frame", "last_frame"}
    }
    values = [
        item.get("media_file_id")
        for item in bindings
        if isinstance(item, dict)
        and item.get("media_kind", "image") == "image"
        and binding_role(item) not in {"first_frame", "last_frame"}
    ]
    values.extend(refs.get("reference_media_ids") or [])
    return list(dict.fromkeys(
        value for value in values
        if type(value) is int and value > 0 and value not in frame_media
    ))


async def _prepare_batch(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    segment_ids: list[int],
    provider_model_id: int,
    parameters: dict[str, Any],
) -> tuple[Project, Episode, EpisodeProductionPlan, ProviderModel, Provider, list[VideoSegment], dict[int, list[int]], dict[str, Any]]:
    project = await session.get(Project, project_id)
    episode = await session.get(Episode, episode_id)
    if project is None or episode is None or episode.project_id != project_id:
        raise NotFoundError("项目或分集不存在")
    from app.services.team_access import same_team
    if not await same_team(session, project.owner_id, owner_id):
        raise NotFoundError("项目不存在")
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode_id)
    )
    if production is None or production.active_plan_id is None:
        raise ConflictError("请先建立本集片段计划")
    plan = await session.get(EpisodeProductionPlan, production.active_plan_id)
    if plan and plan.source_type == "content_frozen":
        raise ConflictError("首帧引用已随片段计划冻结，请在整集规划中显式选择素材，不得自动替换")
    ordered_ids = list(dict.fromkeys(segment_ids))
    segments = list((await session.scalars(
        select(VideoSegment).where(
            VideoSegment.plan_id == production.active_plan_id,
            VideoSegment.episode_id == episode_id,
            VideoSegment.id.in_(ordered_ids),
        ).order_by(VideoSegment.order)
    )).all())
    if plan is None or len(segments) != len(ordered_ids):
        raise ConflictError("片段计划已变化，请刷新后重试")
    if any(item.status == "archived" for item in segments):
        raise ConflictError("已归档片段不能生成首帧")
    pair = (await session.execute(
        select(ProviderModel, Provider)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .where(ProviderModel.id == provider_model_id)
    )).one_or_none()
    if pair is None:
        raise NotFoundError("图片模型不存在")
    image_model, provider = pair
    validate_model_protocol(provider, image_model)
    if not provider.enabled or not image_model.enabled or image_model.model_type != "image":
        raise ConflictError("首帧必须使用已启用的图片模型")
    references_by_segment = {item.id: _reference_media(item) for item in segments}
    if any(references_by_segment.values()) and "reference_images" not in (image_model.capabilities or []):
        raise ConflictError("当前图片模型不支持参考图，无法保持角色与场景一致性")
    max_references = (image_model.default_params or {}).get("max_reference_images", 4)
    if type(max_references) is not int or max_references < 1:
        max_references = 4
    overflow = next((item for item in segments if len(references_by_segment[item.id]) > max_references), None)
    if overflow is not None:
        raise ConflictError(
            f"片段 {overflow.order:02d} 有 {len(references_by_segment[overflow.id])} 张参考图，"
            f"超过图片模型上限 {max_references} 张；请调整引用或更换模型"
        )
    effective = {**(image_model.default_params or {}), **(parameters or {})}
    if not effective.get("resolution"):
        resolutions = effective.get("resolutions")
        if isinstance(resolutions, list) and resolutions and isinstance(resolutions[0], str):
            effective["resolution"] = resolutions[0]
    effective["aspect_ratio"] = (project.creation_settings or {}).get("aspect_ratio") or "16:9"
    return project, episode, plan, image_model, provider, segments, references_by_segment, effective


async def plan_batch(
    session: AsyncSession,
    owner_id: int,
    **kwargs: Any,
) -> dict[str, Any]:
    negative_prompt = kwargs.pop("negative_prompt", None)
    project, episode, plan, model, provider, segments, _references, effective = (
        await _prepare_batch(session, owner_id, **kwargs)
    )
    quotes = [estimate_pricing(model, _frame_prompt(segment), effective) for segment in segments]
    pricing = _aggregate_video_pricing(quotes)
    pricing["reason"] = (
        "按各首帧图片提交参数汇总估算，非渠道实际扣费"
        if pricing.get("amount") is not None
        else "部分或全部首帧图片价格未知；未知不等于免费"
    )
    return {
        "project_id": project.id,
        "episode_id": episode.id,
        "provider_model_id": model.id,
        "provider_name": provider.name,
        "model_id": model.model_id,
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "segment_ids": [segment.id for segment in segments],
        "estimated_count": len(segments),
        "parameters": effective,
        "negative_prompt": negative_prompt,
        "pricing_estimate": pricing,
        "requires_confirmation": True,
    }


async def create_batch(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    segment_ids: list[int],
    provider_model_id: int,
    parameters: dict[str, Any],
    negative_prompt: str | None,
    request_id: str,
    max_cost_cents: int,
    expected_plan_id: int,
    expected_plan_revision: int,
) -> Job:
    prepared = await _prepare_batch(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_ids=segment_ids, provider_model_id=provider_model_id, parameters=parameters,
    )
    project, episode, plan, _image_model, _provider, segments, references_by_segment, effective = prepared
    if plan.id != expected_plan_id or plan.revision != expected_plan_revision:
        raise ConflictError("片段计划已变化，请重新预检首帧生成")
    preview = await plan_batch(
        session, owner_id, project_id=project_id, episode_id=episode_id,
        segment_ids=segment_ids, provider_model_id=provider_model_id,
        parameters=parameters, negative_prompt=negative_prompt,
    )
    estimated_cents = preview["pricing_estimate"].get("estimated_cents")
    if type(estimated_cents) is not int:
        raise ConflictError("首帧费用未知，禁止提交；请先补全图片模型定价")
    if estimated_cents > max_cost_cents:
        raise ConflictError("首帧预估费用已变化，请重新确认")
    ordered_ids = [segment.id for segment in segments]
    fingerprint = _digest({
        "plan_id": plan.id,
        "segment_ids": ordered_ids,
        "provider_model_id": provider_model_id,
        "parameters": effective,
        "negative_prompt": negative_prompt,
    })
    prior = list((await session.scalars(select(Job).where(
        Job.owner_id == owner_id,
        Job.project_id == project_id,
        Job.target_type == TARGET_SEGMENT_FIRST_FRAME_BATCH,
    ))).all())
    for existing in prior:
        if (existing.payload or {}).get("request_id") != request_id:
            continue
        if (existing.payload or {}).get("request_fingerprint") != fingerprint:
            raise ConflictError("请求编号已用于不同的首帧批次")
        return existing

    parent = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type=JOB_TYPE_IMAGE,
        status=JOB_STATUS_PROCESSING,
        progress=0,
        target_type=TARGET_SEGMENT_FIRST_FRAME_BATCH,
        target_id=episode_id,
        payload={
            "request_id": request_id,
            "request_fingerprint": fingerprint,
            "provider_model_id": provider_model_id,
            "plan_id": plan.id,
            "segment_ids": ordered_ids,
            "parameters": effective,
        },
        result={"total": len(segments), "completed": 0, "succeeded": 0,
                "failed": 0, "cancelled": 0, "child_job_ids": [], "children": []},
    )
    session.add(parent)
    await session.flush()
    children: list[Job] = []
    for segment in segments:
        references = references_by_segment[segment.id]
        frame_asset = await _frame_asset(session, project, episode, segment)
        child = await job_service.create_image_job(
            session,
            owner_id,
            project_id=project_id,
            asset_id=frame_asset.id,
            provider_model_id=provider_model_id,
            prompt=asset_service.asset_image_prompt(
                "reference", _frame_prompt(segment), "first_frame"
            ),
            negative_prompt=negative_prompt,
            reference_media_ids=references,
            parameters=effective,
            view_type="first_frame",
            view_label=f"片段 {segment.order:02d} 首帧",
        )
        child.parent_job_id = parent.id
        child.payload = {
            **(child.payload or {}),
            "segment_frame": {
                "plan_id": plan.id,
                "segment_id": segment.id,
                "lineage_key": segment.lineage_key,
                "role": "first_frame",
            },
        }
        children.append(child)
    pricing = _aggregate_video_pricing([
        dict((child.payload or {}).get("pricing_snapshot") or {}) for child in children
    ])
    parent.payload = {**parent.payload, "pricing_snapshot": pricing}
    parent.cost_estimate = pricing.get("estimated_cents")
    parent.result = {**(parent.result or {}),
                     "child_job_ids": [child.id for child in children],
                     "estimated_cost": pricing}
    await session.flush()
    return parent


async def bind_completed_frame(
    session: AsyncSession, job: Job, *, asset_id: int, asset_version_id: int, media_file_id: int
) -> str:
    frame = (job.payload or {}).get("segment_frame")
    if not isinstance(frame, dict) or job.project_id is None:
        return "not_segment_frame"
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.active_plan_id == frame.get("plan_id"))
    )
    segment = await session.get(VideoSegment, frame.get("segment_id"))
    if (
        production is None or segment is None or segment.plan_id != frame.get("plan_id")
        or segment.lineage_key != frame.get("lineage_key")
    ):
        return "plan_changed"
    refs = dict(segment.refs or {})
    bindings = [item for item in refs.get("asset_bindings", []) if isinstance(item, dict)]
    if refs.get("continuity") or any(binding_role(item) == "first_frame" for item in bindings):
        return "candidate_ready"
    project = await session.get(Project, job.project_id)
    binding = await resolve_asset_binding(
        session,
        project,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        media_file_id=media_file_id,
        role="first_frame",
    )
    segment.refs = {
        **refs,
        "asset_bindings": [*bindings, binding],
        "reference_media_ids": list(dict.fromkeys([
            *(refs.get("reference_media_ids") or []), media_file_id,
        ])),
    }
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    if plan is not None:
        plan.revision += 1
    await session.flush()
    return "bound"
