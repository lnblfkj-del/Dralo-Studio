"""Durable parent/child batches for project asset image generation."""

# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_TYPE_IMAGE,
    Asset,
    Job,
    Project,
    ProjectAssetLink,
    ProviderModel,
)
from app.services import asset_service, job_service
from app.services.asset_character_representation import GROUP_CONTRACT, character_profile, is_group
from app.services.costume_generation_service import resolve_mode
from app.services.costume_generation_service import views as costume_views

ASSET_IMAGE_BATCH_CONTRACT = "asset-image-batch.v6"
CHARACTER_REFERENCE_NEGATIVE_PROMPT = (
    "全身视图人物过大，人物贴边，人物越界，分区重叠，裁切头顶，裁切发髻，"
    "裁切头饰，裁切手部，裁切鞋底，裁切脚趾，裁切长袍或裙摆，脚下无留白"
)
TARGET_ASSET_IMAGE_BATCH = "asset_image_batch"
IMAGE_ASSET_TYPES = {"character", "scene", "prop", "costume", "reference"}


def _supported_aspect_ratios(model: ProviderModel) -> list[str]:
    values = (model.default_params or {}).get("aspect_ratios", [])
    return [str(value) for value in values if isinstance(value, str)]


def _character_view_parameters(
    model: ProviderModel, parameters: dict[str, Any], view: dict[str, Any]
) -> dict[str, Any]:
    result = dict(parameters)
    supported = _supported_aspect_ratios(model)
    preferences = [str(value) for value in view["aspect_ratio_preferences"]]
    if supported:
        result["aspect_ratio"] = next(
            (ratio for ratio in preferences if ratio in supported),
            result.get("aspect_ratio") if result.get("aspect_ratio") in supported else supported[0],
        )
    else:
        result["aspect_ratio"] = preferences[0]
    return result


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _effective_image_parameters(project: Project, parameters: dict[str, Any]) -> dict[str, Any]:
    effective = dict(parameters or {})
    ratio = effective.get("aspect_ratio")
    if ratio in (None, "", "default", "project", "模型默认"):
        ratio = (project.creation_settings or {}).get("aspect_ratio")
    if ratio in (None, "", "default", "project", "模型默认"):
        ratio = "16:9"
    effective["aspect_ratio"] = ratio
    return effective


async def latest(session: AsyncSession, project: Project) -> Job | None:
    return await session.scalar(
        select(Job)
        .where(
            Job.owner_id == project.owner_id,
            Job.project_id == project.id,
            Job.target_type == TARGET_ASSET_IMAGE_BATCH,
            Job.deleted_at.is_(None),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


async def create(
    session: AsyncSession,
    project: Project,
    *,
    asset_ids: list[int],
    provider_model_id: int,
    negative_prompt: str | None,
    parameters: dict[str, Any],
    request_id: str,
    generation_mode: str = "missing",
    scope: str = "selected",
    preview_token: str | None = None,
    prepare_prompts: bool = False,
    text_model_id: int | None = None,
) -> Job:
    from app.services.asset_batch_planning import effective_anchor, preview
    from app.services.asset_batch_scheduler import lock_transition
    from app.services.asset_consistency_service import lock_project
    await lock_transition(session)
    await lock_project(session, project.id)
    await session.refresh(project)
    if generation_mode not in {"missing", "regenerate"}:
        raise ConflictError("无效的图片生成模式")
    ordered_ids = list(dict.fromkeys(asset_ids))
    request_fingerprint = _fingerprint(
        {
            "asset_ids": ordered_ids,
            "provider_model_id": provider_model_id,
            "negative_prompt": negative_prompt,
            "parameters": parameters,
            "generation_mode": generation_mode,
            "scope": scope,
            "preview_token": preview_token,
            "prepare_prompts": prepare_prompts, "text_model_id": text_model_id,
        }
    )
    existing = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.owner_id == project.owner_id,
                    Job.project_id == project.id,
                    Job.target_type == TARGET_ASSET_IMAGE_BATCH,
                )
            )
        ).all()
    )
    for job in existing:
        if (job.payload or {}).get("request_id") != request_id:
            continue
        if (job.payload or {}).get("request_fingerprint") != request_fingerprint:
            raise ConflictError("请求编号已用于不同的资产图片批次")
        return job

    parameters = _effective_image_parameters(project, parameters)
    image_model = await session.get(ProviderModel, provider_model_id)
    if image_model is None:
        raise ConflictError("图片模型不存在")
    parameters = {**(image_model.default_params or {}), **parameters}

    plan = await preview(session, project, asset_ids=ordered_ids, provider_model_id=provider_model_id,
                         negative_prompt=negative_prompt, parameters=parameters,
                         generation_mode=generation_mode, scope=scope,
                         prepare_prompts=prepare_prompts, text_model_id=text_model_id)
    if not preview_token or not hmac.compare_digest(preview_token, plan["preview_token"]):
        raise ConflictError("图片批次预览已变化，请重新确认范围与费用")
    if not plan["can_create"]:
        raise ConflictError("图片批次暂不可创建，请检查前置条件", details={"preview": plan})
    selected_ids = ordered_ids
    ordered_ids = plan["asset_ids"]

    rows = list(
        (
            await session.execute(
                select(Asset, ProjectAssetLink)
                .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
                .where(
                    ProjectAssetLink.project_id == project.id,
                    Asset.id.in_(ordered_ids),
                )
            )
        ).all()
    )
    by_id = {asset.id: (asset, link) for asset, link in rows}
    if len(by_id) != len(ordered_ids):
        raise ConflictError("资产清单包含不属于当前项目的资产")

    eligible: list[tuple[Asset, str]] = []
    groups = {asset_id: character_profile(asset.attributes, link.production_data)
              for asset_id, (asset, link) in by_id.items()}
    skipped = plan["skipped"]
    for asset_id in ordered_ids:
        asset, link = by_id[asset_id]
        prompt = effective_anchor(asset, link, dependency_only=asset_id not in selected_ids)
        eligible.append((asset, prompt))
    planned_view_count = plan["planned_view_count"]

    parent = Job(
        owner_id=project.owner_id,
        project_id=project.id,
        job_type=JOB_TYPE_IMAGE,
        status=JOB_STATUS_PROCESSING,
        progress=0,
        target_type=TARGET_ASSET_IMAGE_BATCH,
        target_id=project.id,
        payload={
            "asset_image_batch_contract": ASSET_IMAGE_BATCH_CONTRACT,
            "request_id": request_id,
            "request_fingerprint": request_fingerprint,
            "provider_model_id": provider_model_id,
            "asset_ids": ordered_ids,
            "parameters": parameters,
            "generation_mode": generation_mode,
            "character_reference_contract": asset_service.CHARACTER_REFERENCE_SHEET_KEY,
            "asset_reference_contracts": asset_service.ASSET_REFERENCE_CONTRACTS,
            "planned_view_count": planned_view_count,
            "expected_total": planned_view_count,
            "batch_plan": plan,
            "selected_asset_ids": selected_ids,
            "scope": scope,
        },
        result={
            "requested": len(selected_ids),
            "expanded": len(plan["expanded_asset_ids"]),
            "eligible": len(eligible),
            "skipped": skipped,
            "total": planned_view_count,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "child_job_ids": [],
            "children": [],
        },
    )
    session.add(parent)
    await session.flush()

    children: list[Job] = []
    for asset, anchor in eligible:
        costume_mode = await resolve_mode(session, project.id, asset, parameters)
        child_parameters = {**parameters, **({"costume_mode": costume_mode} if costume_mode else {})}
        prompt = plan["expanded_prompts"][str(asset.id)] or f"ASSET_PROMPT_PENDING_{asset.id}"
        reference_media_ids = []  # Exact adopted bindings are frozen by the durable gate.
        character_view = (
            asset_service.character_reference_sheet_views()[0]
            if asset.asset_type == "character" and not is_group(groups[asset.id]) else None
        )
        view_type, view_label = (
            (str(character_view["view_type"]), str(character_view["view_label"]))
            if character_view else asset_service.normalize_asset_view(
                asset.asset_type, source_text=anchor
            )
        )
        generation_contract = asset_service.asset_generation_contract(asset.asset_type)
        if asset.asset_type == "character" and is_group(groups[asset.id]):
            generation_contract = GROUP_CONTRACT
        if costume_mode == "garment_only":
            generation_contract = "costume-garment.v1"
        reference_negative = (
            CHARACTER_REFERENCE_NEGATIVE_PROMPT
            if character_view else "人物，人体，模特，人台，文字，水印" if costume_mode == "garment_only" else asset_service.asset_reference_negative_prompt(asset.asset_type)
        )
        child = await job_service.create_image_job(
            session,
            project.owner_id,
            project_id=project.id,
            asset_id=asset.id,
            provider_model_id=provider_model_id,
            prompt=(
                asset_service.character_reference_prompt(prompt, str(character_view["key"]))
                if character_view else asset_service.asset_image_prompt(asset.asset_type, prompt, view_type, costume_mode=costume_mode, character_profile=groups[asset.id])
            ),
            negative_prompt="\n".join(filter(None, [negative_prompt, reference_negative])) or None,
            reference_media_ids=reference_media_ids,
            parameters=(
                _character_view_parameters(image_model, child_parameters, character_view)
                if character_view else child_parameters
            ),
            view_type=view_type,
            view_label=view_label,
            defer_consistency=True,
            asset_generation_contract=generation_contract,
        )
        child.parent_job_id = parent.id
        if generation_contract:
            child.payload = {
                **child.payload,
                "asset_generation_contract": generation_contract,
                "version_tags": [f"contract:{generation_contract}"],
            }
        if character_view:
            child.payload = {
                **child.payload,
                "character_reference_sheet": {
                    "contract": asset_service.CHARACTER_REFERENCE_SHEET_KEY,
                    "view_key": character_view["key"],
                    "identity_prompt": prompt,
                    "source_reference_media_ids": reference_media_ids,
                },
                "requires_manual_review": True,
                "version_tags": [
                    *(child.payload.get("version_tags") or []),
                    f"character-sheet-view:{character_view['key']}",
                    "qa:manual-review-required",
                ],
            }
        children.append(child)
        child.payload = {**child.payload, "asset_type": asset.asset_type}
        if character_view:
            for extra_view in asset_service.character_reference_sheet_views()[1:]:
                extra = await job_service.create_image_job(
                    session, project.owner_id, project_id=project.id, asset_id=asset.id,
                    provider_model_id=provider_model_id,
                    prompt=asset_service.character_reference_prompt(prompt, str(extra_view["key"])),
                    negative_prompt="\n".join(filter(None, [negative_prompt, reference_negative])) or None,
                    reference_media_ids=[], parameters=_character_view_parameters(image_model, parameters, extra_view),
                    view_type=str(extra_view["view_type"]), view_label=str(extra_view["view_label"]),
                    defer_consistency=True, asset_generation_contract=generation_contract,
                )
                extra.parent_job_id = parent.id
                extra.payload = {**extra.payload, "asset_type": asset.asset_type,
                    "character_reference_sheet": {**child.payload["character_reference_sheet"], "view_key": extra_view["key"]},
                    "requires_manual_review": True,
                    "version_tags": [f"contract:{generation_contract}", f"character-sheet-view:{extra_view['key']}", "qa:manual-review-required"]}
                children.append(extra)
        if asset.asset_type == "costume":
            for key, label in costume_views(parameters.get("costume_views"))[1:]:
                extra = await job_service.create_image_job(
                    session, project.owner_id, project_id=project.id, asset_id=asset.id,
                    provider_model_id=provider_model_id,
                    prompt=asset_service.asset_image_prompt(asset.asset_type, prompt, "angle", costume_mode=costume_mode, costume_direction=label)
                           + f"\n本次唯一观察方向：{label}，保持同一套服装，不做拼版。",
                    negative_prompt="\n".join(filter(None, [negative_prompt, reference_negative])) or None,
                    reference_media_ids=reference_media_ids,
                    parameters={**parameters, "costume_mode": costume_mode, "costume_direction": label},
                    view_type="angle", view_label=f"服装{label}",
                    defer_consistency=True,
                    asset_generation_contract=generation_contract,
                )
                extra.parent_job_id = parent.id
                extra.payload = {**extra.payload, "asset_adoption_key": f"costume:{costume_mode}:{key}",
                                 "asset_generation_contract": generation_contract, "asset_type": asset.asset_type}
                children.append(extra)

    from app.services.asset_batch_scheduler import prepare_batch, stage_children
    await stage_children(session, parent, children, plan)
    if plan.get("prompt_asset_ids"):
        from app.services.asset_batch_prompt_preparation import stage_prompts
        await stage_prompts(session, project, parent, children, plan)
    await prepare_batch(session, parent)

    pricing = plan["estimated_cost"]
    parent.payload = {**parent.payload, "pricing_snapshot": pricing}
    parent.cost_estimate = pricing.get("estimated_cents")
    parent.result = {
        **(parent.result or {}),
        "child_job_ids": [child.id for child in children],
        "planned_view_count": planned_view_count,
        "estimated_cost": pricing,
    }
    await session.flush()
    if children:
        await job_service.aggregate_parent_job(session, children[0].id)
    return parent
