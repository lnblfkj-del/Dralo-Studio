"""Explicit, audited joins for immutable V1 responses; never fuzzy repair."""

# ruff: noqa: RUF001

import hashlib
import json
from copy import deepcopy

from pydantic import ValidationError

from app.core.errors import ConflictError
from app.models import Job, utcnow
from app.schemas.creation_assets import ScriptAssetBreakdownResult, ScriptAssetUsageLink
from app.services.creation_breakdown_planning import REQUIREMENT_RESULT_KEYS, normalized_asset_key
from app.services.structured_output_service import parse_json_document


def inspect_saved_links(text: str) -> list[dict]:
    decoded, _ = parse_json_document(text, "历史资产拆解响应")
    if not isinstance(decoded, dict) or not isinstance(decoded.get("usage_records"), list):
        return []
    # Validate the assets independently without changing the saved response.
    document = ScriptAssetBreakdownResult.model_validate({**decoded, "usage_records": []})
    stored = {}
    exact_names = {}
    for kind, group in REQUIREMENT_RESULT_KEYS.items():
        for index, asset in enumerate(getattr(document, group)):
            target = f"{group}.{index}"
            stored.setdefault(asset.attributes["asset_key"], []).append(target)
            for name in [asset.name, *asset.aliases]:
                key = normalized_asset_key(kind, name)
                exact_names.setdefault(key, set()).add(target)
    issues = []
    for index, usage in enumerate(decoded["usage_records"]):
        if not isinstance(usage, dict) or not isinstance(usage.get("asset_key"), str):
            continue
        key = usage["asset_key"]
        if len(stored.get(key, [])) == 1:
            continue
        suggestions = sorted(exact_names.get(key, set()))
        issues.append(
            {
                "field": f"usage_records.{index}.asset_key",
                "status": "ambiguous" if len(stored.get(key, [])) > 1 else "missing",
                "suggested_targets": suggestions,
                "review_required": True,
            }
        )
    return issues


def link_review(text: str) -> dict:
    decoded, _ = parse_json_document(text, "历史资产拆解响应")
    if not isinstance(decoded, dict) or not isinstance(decoded.get("usage_records"), list):
        raise ConflictError("该响应没有可核对的资产使用记录")
    if len(decoded["usage_records"]) > 500:
        raise ConflictError("该响应使用记录超过上限，不能仅通过关联核对恢复")
    try:
        document = ScriptAssetBreakdownResult.model_validate({**decoded, "usage_records": []})
        usages = [ScriptAssetUsageLink.model_validate(row) for row in decoded["usage_records"]]
    except ValidationError as exc:
        raise ConflictError("响应还存在字段缺失或格式错误，不能仅通过资产关联核对恢复") from exc
    targets = []
    keys = set()
    for kind, group in REQUIREMENT_RESULT_KEYS.items():
        for index, asset in enumerate(getattr(document, group)):
            key = asset.attributes["asset_key"]
            if key in keys:
                raise ConflictError("响应存在重复资产键，需要重新生成该范围，不能猜测对应关系")
            keys.add(key)
            targets.append(
                {
                    "target": f"{group}.{index}",
                    "kind": kind,
                    "asset_key": key,
                    "name": asset.name,
                    "aliases": asset.aliases,
                    "description": asset.description,
                }
            )
    issues = inspect_saved_links(text)
    for issue in issues:
        usage = usages[int(issue["field"].split(".")[1])]
        kind = usage.asset_key.strip().split(":", 1)[0]
        options = [target["target"] for target in targets if target["kind"] == kind]
        if not options:
            raise ConflictError("出错记录没有可核对的同类资产，不能创建或猜测新资产")
        issue.update(
            {
                "asset_key": usage.asset_key,
                "kind": kind,
                "episode_number": usage.episode_number,
                "scene": usage.scene,
                "source_excerpt": usage.source_excerpt,
                "allowed_targets": options,
            }
        )
        issue["suggested_targets"] = [
            value for value in issue["suggested_targets"] if value in options
        ]
    if not issues:
        raise ConflictError("该响应没有需要人工核对的资产关联错误")
    return {"issues": issues, "targets": targets}


async def review_response(session, job, stored) -> dict:
    from app.services.creation_breakdown_sources import validate_script_asset_breakdown_job_sources

    parameters = (job.payload or {}).get("parameters") or {}
    if (
        job.target_type != "script_asset_breakdown_batch"
        or int(parameters.get("asset_extraction_contract_version", 1)) != 1
        or job.status != "failed"
        or job.error_code != "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
        or stored is None
        or stored.status != "available"
        or stored.expires_at.replace(tzinfo=None) <= utcnow().replace(tzinfo=None)
    ):
        raise ConflictError("该任务没有可核对的历史资产失败响应")
    payload = job.payload or {}
    submission = payload.get("text_submission") or {}
    recovery = payload.get("response_recovery") or {}
    if (
        hashlib.sha256(stored.response_text.encode("utf-8")).hexdigest() != stored.response_sha256
        or (
            submission.get("response_sha256")
            and submission["response_sha256"] != stored.response_sha256
        )
        or (submission.get("call_id") and submission["call_id"] != stored.call_id)
    ):
        raise ConflictError("已保存响应完整性校验失败，请重新核对任务")
    if str(submission.get("finish_reason") or recovery.get("finish_reason") or "").lower() in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }:
        raise ConflictError("模型输出已截断，人工关联不能补齐缺失内容")
    item = await validate_script_asset_breakdown_job_sources(
        session, job, require_parent_active=False
    )
    parent = await session.get(Job, job.parent_job_id) if job.parent_job_id else None
    if parent is not None:
        await session.refresh(parent)
    progress = (item.settings or {}).get("asset_breakdown") or {}
    if (
        parent is None
        or parent.status not in {"failed", "processing"}
        or progress.get("parent_job_id") != parent.id
        or (job.resolution or {}).get("status") in {"superseded", "replacement_pending"}
    ):
        raise ConflictError("该拆解批次已取消、被替代或不是当前版本，不能核对旧结果")
    return {
        **link_review(stored.response_text),
        "response_sha256": stored.response_sha256,
        "episode_numbers": parameters.get("episode_numbers") or [],
        "pending_audio_calls": len((parent.payload or {}).get("pending_audio_units") or [])
        if parameters.get("requirement_group") == "visual"
        else 0,
    }


async def corrected_response(
    session,
    job,
    stored,
    corrections,
    expected_sha,
    *,
    acknowledge_pending_audio_calls=False,
    expected_pending_audio_calls=0,
) -> tuple[str, int]:
    review = await review_response(session, job, stored)
    if expected_sha != review["response_sha256"]:
        raise ConflictError("已保存响应已变化，请重新打开资产关联核对")
    pending = review["pending_audio_calls"]
    if expected_pending_audio_calls != pending or (pending and not acknowledge_pending_audio_calls):
        raise ConflictError("后续声音提取需要调用文本模型，请核对请求数量并明确确认费用后继续")
    issues = {issue["field"]: issue for issue in review["issues"]}
    if (
        not corrections
        or set(corrections) != set(issues)
        or any(
            target not in issues[field]["allowed_targets"] for field, target in corrections.items()
        )
    ):
        raise ConflictError("请逐条选择出错记录对应的现有同类资产，不允许修改其他字段")
    decoded, _ = parse_json_document(stored.response_text, "历史资产拆解响应")
    projection = deepcopy(decoded)
    targets = {target["target"]: target["asset_key"] for target in review["targets"]}
    for field, target in corrections.items():
        projection["usage_records"][int(field.split(".")[1])]["asset_key"] = targets[target]
    # Validate the entire projection, not just the corrected fields. The stored text stays immutable.
    ScriptAssetBreakdownResult.model_validate(projection)
    return json.dumps(projection, ensure_ascii=False), pending
