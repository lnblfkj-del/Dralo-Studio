"""Explicit manual association of rejected candidates; no inference or model calls."""

from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Asset, ProjectAssetLink
from app.services.asset_extraction_scheduling import lock_breakdown_session
from app.services.script_source_snapshot import script_snapshot_is_current


def can_keep_in_script(pending):
    return pending.get("kind") in {"prop", "music", "ambience", "sound_effect"} and not pending.get("declared_necessity")


async def resolve_item(session, item, payload, actor_id):
    await lock_breakdown_session(session, item)
    settings = deepcopy(item.settings)
    breakdown = settings.get("asset_breakdown") or {}
    if (breakdown.get("parent_job_id") != payload.expected_parent_job_id
            or breakdown.get("input_fingerprint") != payload.input_fingerprint
            or not await script_snapshot_is_current(session, item.project_id, payload.input_fingerprint)):
        raise ConflictError("拆解或正文版本已变化，请刷新后核对；未调用模型")
    if breakdown.get("status") not in {"awaiting_confirmation", "completed"}:
        raise ConflictError("请等待本轮提取结束后核对")
    identity = f"{payload.job_id}:{payload.index}"
    receipts = breakdown.setdefault("deferred_review_receipts", {})
    if identity in receipts:
        if receipts[identity]["request"] != payload.model_dump():
            raise ConflictError("该素材已经按另一项选择处理，请刷新")
        return receipts[identity]
    scope = (breakdown.get("review_items_by_scope") or {}).get(payload.scope_key) or {}
    pending = next((row for row in scope.get("items", []) if row["index"] == payload.index), None)
    if scope.get("job_id") != payload.job_id or pending is None:
        raise ConflictError("待核对项已经变化，请刷新")
    asset = None
    if payload.disposition == "script_only":
        if not can_keep_in_script(pending):
            raise ConflictError("身份、造型或已声明独立制作需求的素材不能降级为普通文字，请关联素材")
    else:
        link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == item.project_id, ProjectAssetLink.asset_id == payload.asset_id,
            ProjectAssetLink.production_archived.is_(False)))
        asset = await session.get(Asset, payload.asset_id)
        if not link or not asset:
            raise ConflictError("请选择本项目中已建立且未归档的素材")
        expected = "voice" if pending.get("kind") in {"character_voice", "music", "ambience", "sound_effect"} else pending.get("kind")
        allowed = {"character", "costume", "scene", "prop", "voice"}
        if asset.asset_type not in allowed or (expected in allowed and asset.asset_type != expected):
            raise ConflictError("素材类型不匹配，不能用其他类型代替待核对项")
    receipt = {"request": payload.model_dump(), "actor_id": actor_id,
               "resolved_at": datetime.now(UTC).isoformat(), "asset_name": asset.name if asset else None,
               "original_item": pending, "episode_numbers": scope["episode_numbers"]}
    receipts[identity] = receipt
    scope["items"] = [row for row in scope["items"] if row["index"] != payload.index]
    if not scope["items"]:
        breakdown["review_items_by_scope"].pop(payload.scope_key)
    breakdown["coverage_status"] = "requires_review" if breakdown.get("review_items_by_scope") else "not_exhaustively_verified"
    # Association acknowledges a manual review, never rewrites asset identity or source facts.
    item.settings = settings
    await session.flush()
    return receipt
