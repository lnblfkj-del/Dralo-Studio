"""Durable parent/child batches for project asset image generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_TYPE_IMAGE,
    Asset,
    AssetVersion,
    Job,
    Project,
    ProjectAssetLink,
    ProviderModel,
)
from app.services import asset_service, job_service
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing

ASSET_IMAGE_BATCH_CONTRACT = "asset-image-batch.v5"
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
) -> Job:
    if generation_mode not in {"missing", "regenerate"}:
        raise ConflictError("无效的图片生成模式")
    ordered_ids = list(dict.fromkeys(asset_ids))
    parameters = _effective_image_parameters(project, parameters)
    image_model = await session.get(ProviderModel, provider_model_id)
    if image_model is None:
        raise ConflictError("图片模型不存在")
    request_fingerprint = _fingerprint(
        {
            "asset_ids": ordered_ids,
            "provider_model_id": provider_model_id,
            "negative_prompt": negative_prompt,
            "parameters": parameters,
            "generation_mode": generation_mode,
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

    active_asset_ids = set(
        (
            await session.scalars(
                select(Job.target_id).where(
                    Job.project_id == project.id,
                    Job.target_type == "asset",
                    Job.target_id.in_(ordered_ids),
                    Job.status.not_in(TERMINAL_STATUSES),
                    Job.deleted_at.is_(None),
                )
            )
        ).all()
    )
    version_rows = list((await session.scalars(
        select(AssetVersion).where(
            AssetVersion.asset_id.in_(ordered_ids),
            AssetVersion.review_status != "archived",
        )
    )).all())
    final_asset_ids = {
        version.asset_id for version in version_rows
        if version.is_final and version.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE
    }
    sheet_views_by_asset: dict[int, set[str]] = {}
    for version in version_rows:
        for tag in version.tags or []:
            if isinstance(tag, str) and tag.startswith("character-sheet-view:"):
                sheet_views_by_asset.setdefault(version.asset_id, set()).add(tag.split(":", 1)[1])
    required_sheet_views = {
        str(view["key"]) for view in asset_service.character_reference_sheet_views()
    }
    complete_character_sheets = {
        asset_id for asset_id, views in sheet_views_by_asset.items()
        if required_sheet_views.issubset(views)
    }

    eligible: list[tuple[Asset, str]] = []
    skipped: list[dict[str, Any]] = []
    for asset_id in ordered_ids:
        asset, link = by_id[asset_id]
        prompt = (
            link.production_data.get("prompt_anchor")
            if "prompt_anchor" in (link.production_data or {})
            else asset.prompt_anchor
        )
        if asset.asset_type == "character" and not str(prompt or "").strip():
            prompt = asset.description
        code = None
        reason = None
        if asset.asset_type not in IMAGE_ASSET_TYPES:
            code, reason = "unsupported_media_type", "该资产类型不使用图片模型"
        elif asset.project_id != project.id:
            code, reason = "shared_asset_requires_local_variant", "公共素材需先建立项目变体"
        elif link.production_archived:
            code, reason = "archived", "资产已归档，请先恢复"
        elif generation_mode == "missing" and asset.asset_type == "character" and asset.id in complete_character_sheets:
            code, reason = "already_ready", "已有角色设定图"
        elif generation_mode == "missing" and asset.asset_type != "character" and ((link.production_data or {}).get("adoptions") or asset.id in final_asset_ids):
            code, reason = "already_ready", "已有采用素材或最终版"
        elif not isinstance(prompt, str) or not prompt.strip():
            code, reason = "missing_prompt", "缺少资产一致性提示词"
        elif asset.id in active_asset_ids:
            code, reason = "active_job", "已有进行中的图片任务"
        if code:
            skipped.append(
                {"asset_id": asset.id, "asset_name": asset.name, "code": code, "reason": reason}
            )
        else:
            eligible.append((asset, prompt.strip()))

    if not eligible:
        reasons = "；".join(f"{item['asset_name']}：{item['reason']}" for item in skipped[:10])
        raise ConflictError(f"所选资产均不可生成图片。{reasons}" + ("；其余原因请缩小选择范围查看" if len(skipped) > 10 else ""))
    planned_view_count = sum(
        len(asset_service.character_reference_sheet_views())
        if asset.asset_type == "character" else 1
        for asset, _prompt in eligible
    )

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
        },
        result={
            "requested": len(ordered_ids),
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
        prompt, references = await asset_service.expand_prompt(session, project.id, anchor)
        reference_media_ids = [
            version.media_file_id
            for referenced in references
            for version in referenced["versions"]
            if version.is_final
        ]
        character_view = (
            asset_service.character_reference_sheet_views()[0]
            if asset.asset_type == "character" else None
        )
        view_type, view_label = (
            (str(character_view["view_type"]), str(character_view["view_label"]))
            if character_view else asset_service.normalize_asset_view(
                asset.asset_type, source_text=anchor
            )
        )
        generation_contract = asset_service.asset_generation_contract(asset.asset_type)
        reference_negative = (
            CHARACTER_REFERENCE_NEGATIVE_PROMPT
            if character_view else asset_service.asset_reference_negative_prompt(asset.asset_type)
        )
        child = await job_service.create_image_job(
            session,
            project.owner_id,
            project_id=project.id,
            asset_id=asset.id,
            provider_model_id=provider_model_id,
            prompt=(
                asset_service.character_reference_prompt(prompt, str(character_view["key"]))
                if character_view else asset_service.asset_image_prompt(asset.asset_type, prompt, view_type)
            ),
            negative_prompt="\n".join(filter(None, [negative_prompt, reference_negative])) or None,
            reference_media_ids=reference_media_ids,
            parameters=(
                _character_view_parameters(image_model, parameters, character_view)
                if character_view else parameters
            ),
            view_type=view_type,
            view_label=view_label,
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

    quotes = []
    for child in children:
        quote = dict((child.payload or {}).get("pricing_snapshot") or {})
        copies = (
            len(asset_service.character_reference_sheet_views())
            if (child.payload or {}).get("character_reference_sheet") else 1
        )
        quotes.extend(dict(quote) for _index in range(copies))
    pricing = _aggregate_video_pricing(quotes)
    parent.payload = {**parent.payload, "pricing_snapshot": pricing}
    parent.cost_estimate = pricing.get("estimated_cents")
    parent.result = {
        **(parent.result or {}),
        "child_job_ids": [child.id for child in children],
        "planned_view_count": planned_view_count,
        "estimated_cost": pricing,
    }
    await session.flush()
    return parent
