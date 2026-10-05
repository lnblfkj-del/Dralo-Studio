"""Shared A2 asset prompt proposal executor used by UI buttons and Agent tools."""

# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, GenerationFailedError
from app.models import Job, Project, ProjectAssetLink
from app.services import asset_service, business_executor_service, job_service
from app.services.asset_visual_identity import project_context, retain_visual_facts, visual_profile

TARGET_ASSET_PROMPT_PROPOSAL = "asset_prompt_proposal"


async def create_proposal_job(
    session: AsyncSession,
    project: Project,
    *,
    asset_ids: list[int],
    provider_model_id: int | None,
    request_id: str,
    parameters: dict[str, Any],
) -> Job:
    executor = await business_executor_service.resolve_execution(
        session,
        "asset_prompt_generator",
        requested_model_id=provider_model_id,
    )
    model = executor.get("model")
    if model is None:
        raise ConflictError("资产提示词生成器尚未配置文本模型")
    assets = await asset_service.list_assets(session, project.id)
    by_id = {int(asset["id"]): asset for asset in assets}
    ordered_ids = list(dict.fromkeys(asset_ids))
    if not ordered_ids or any(asset_id not in by_id for asset_id in ordered_ids):
        raise ConflictError("资产清单为空或包含不属于当前项目的资产")
    overlay_rows = list(
        (await session.scalars(
            select(ProjectAssetLink).where(
                ProjectAssetLink.project_id == project.id,
                ProjectAssetLink.asset_id.in_(ordered_ids),
            )
        )).all()
    )
    links_by_asset = {row.asset_id: row for row in overlay_rows}
    prompt_overrides = {
        row.asset_id: row.production_data["prompt_anchor"]
        for row in overlay_rows
        if "prompt_anchor" in (row.production_data or {})
    }
    snapshots = [
        {
            "id": asset["id"],
            "type": asset["asset_type"],
            "name": asset["name"],
            "description": asset["description"],
            "attributes": asset["attributes"] or {},
            "profile": visual_profile(asset["attributes"], links_by_asset[asset["id"]].production_data, asset["description"]),
            "current_prompt": prompt_overrides.get(asset["id"], asset["prompt_anchor"]),
            "production_revision": links_by_asset[asset["id"]].production_revision,
        }
        for asset in (by_id[asset_id] for asset_id in ordered_ids)
    ]
    visual_context = await project_context(session, project)
    fingerprint_value = {
        "project_id": project.id,
        "asset_ids": ordered_ids,
        "model_id": model["id"],
        "executor_revision": executor["executor_revision"],
        "skills": [
            [item["skill_id"], item["selected_version"]]
            for item in executor["skills"]
        ],
        "parameters": parameters,
        "snapshots": snapshots,
        "visual_context": visual_context,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.owner_id == project.owner_id,
                    Job.project_id == project.id,
                    Job.target_type == TARGET_ASSET_PROMPT_PROPOSAL,
                )
            )
        ).all()
    )
    for job in existing:
        if (job.payload or {}).get("request_id") != request_id:
            continue
        if (job.payload or {}).get("input_fingerprint") != fingerprint:
            raise ConflictError("请求编号已用于其他资产提示词内容")
        return job

    prompt = (
        "你是资产一致性提示词业务执行器。只返回严格 JSON，不要 Markdown。"
        "输出格式为 {\"assets\":[{\"id\":1,\"prompt\":\"...\"}]}。"
        "必须逐项保留输入资产 ID；使用中文、具体且可视化；不写剧情，不提交图片，"
        "不得返回清单外的资产。\n"
        "角色提示词必须包含已给profile中的外貌、年龄和完整服装（尤其下装、长裤与鞋履）；不得用风格覆盖人物身份、具体衣裤类型与年代。资料没有的信息不得伪称已确认。\n"
        f"执行 Skill：{json.dumps([{'key': item['key'], 'version': item['selected_version'], 'instruction': item.get('snapshot', {}).get('instruction', '')} for item in executor['skills']], ensure_ascii=False)}\n"
        "本次只提供下列资产文字快照，未提供全剧或图片像素。仅据此生成资产提示词，"
        "不得宣称已看图或完成全剧连续性检查；输出仍限定为 assets 中的 id 和 prompt。\n"
        f"项目背景：{json.dumps(visual_context, ensure_ascii=False)}\n"
        f"资产快照：{json.dumps(snapshots, ensure_ascii=False)}"
    )
    job = await job_service.create_text_job(
        session,
        project.owner_id,
        provider_model_id=int(model["id"]),
        prompt=prompt,
        project_id=project.id,
        parameters={
            "response_format": {"type": "json_object"},
            **parameters,
        },
    )
    job.target_type = TARGET_ASSET_PROMPT_PROPOSAL
    job.target_id = project.id
    job.payload = {
        **job.payload,
        "request_id": request_id,
        "input_fingerprint": fingerprint,
        "asset_ids": ordered_ids,
        "asset_snapshots": snapshots,
        "visual_context": visual_context,
        "business_executor": executor,
        "tool_key": "asset.prompt.generate",
        "auto_apply": True,
    }
    await session.flush()
    return job


def _parse_prompt_assets(text: str) -> dict[int, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GenerationFailedError("提示词优化结果不是有效 JSON，未修改资产") from exc
    rows = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise GenerationFailedError("提示词优化结果缺少 assets 清单，未修改资产")
    proposals: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        prompt = str(row.get("prompt") or "").strip()
        if prompt and len(prompt) <= 4000:
            proposals[int(row["id"])] = prompt
    if not proposals:
        raise GenerationFailedError("提示词优化未返回可用内容，未修改资产")
    return proposals


async def finalize_auto_apply(
    session: AsyncSession,
    job: Job,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Apply optimized prompts only when the captured production revision is unchanged."""
    proposals = _parse_prompt_assets(str(result.get("text") or ""))
    snapshots = {
        int(row["id"]): row
        for row in (job.payload or {}).get("asset_snapshots", [])
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    }
    applied: list[int] = []
    unchanged: list[int] = []
    skipped: list[dict[str, Any]] = []
    for asset_id in (job.payload or {}).get("asset_ids", []):
        snapshot = snapshots.get(int(asset_id))
        proposal = proposals.get(int(asset_id))
        if snapshot is None or proposal is None:
            skipped.append({"asset_id": int(asset_id), "reason": "模型未返回有效提示词"})
            continue
        proposal = retain_visual_facts(proposal, snapshot, (job.payload or {}).get("visual_context"))
        if len(proposal) > 4000:
            skipped.append({"asset_id": int(asset_id), "reason": "保留角色资料后超过提示词长度限制，未截断或覆盖"})
            continue
        if proposal == str(snapshot.get("current_prompt") or "").strip():
            unchanged.append(int(asset_id))
            continue
        link = await session.scalar(
            select(ProjectAssetLink).where(
                ProjectAssetLink.project_id == job.project_id,
                ProjectAssetLink.asset_id == int(asset_id),
            )
        )
        expected_revision = int(snapshot.get("production_revision") or 0)
        if link is None or link.production_archived or link.production_revision != expected_revision:
            skipped.append({"asset_id": int(asset_id), "reason": "资产已在其他页面修改或归档，未覆盖"})
            continue
        data = {**(link.production_data or {}), "prompt_anchor": proposal}
        written = await session.execute(
            update(ProjectAssetLink)
            .where(
                ProjectAssetLink.id == link.id,
                ProjectAssetLink.production_revision == expected_revision,
                ProjectAssetLink.production_archived.is_(False),
            )
            .values(
                production_data=data,
                production_revision=expected_revision + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if written.rowcount == 1:
            applied.append(int(asset_id))
        else:
            skipped.append({"asset_id": int(asset_id), "reason": "资产并发修改，未覆盖"})
    return {
        **result,
        "prompt_application": {
            "mode": "automatic",
            "requested": len((job.payload or {}).get("asset_ids", [])),
            "applied": applied,
            "unchanged": unchanged,
            "skipped": skipped,
        },
    }
