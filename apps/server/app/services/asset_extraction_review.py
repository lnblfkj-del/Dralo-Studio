"""A3 bounded review of completed V2 scopes, without provider calls."""

# ruff: noqa: RUF001

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import ConflictError
from app.models import CreationSession, Job
from app.services.asset_extraction_scheduling import lock_breakdown_session


def candidate_signature(candidate: dict[str, Any]) -> str:
    attributes = candidate.get("attributes") or {}
    content = {
        key: candidate.get(key)
        for key in ("name", "description", "aliases", "episode_numbers", "usage_records")
    }
    content["identity"] = attributes.get("extraction_identity")
    content["facts"] = attributes.get("facts")
    content["conflicts"] = attributes.get("identity_fact_conflicts")
    if attributes.get("field_trust"):
        content["field_trust"] = attributes["field_trust"]
        content["production_fields"] = attributes.get("production_fields")
    if attributes.get("character_identity"):
        content["character_identity"] = attributes["character_identity"]
        content["character_alias_claims"] = attributes.get("character_alias_claims")
    if attributes.get("manual_production_reviews"):
        content["manual_production_reviews"] = attributes["manual_production_reviews"]
    if any(record.get("relation_version") for record in attributes.get("necessity_records") or []):
        content["necessity"] = attributes.get("necessity_records")
        content["appearance_states"] = attributes.get("appearance_states")
        content["review_reasons"] = attributes.get("review_reasons")
        if attributes.get("appearance_completion"):
            content["default_appearance_sources"] = attributes.get("default_appearance_sources")
            content["appearance_state_links"] = attributes.get("appearance_state_links")
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def apply_confirmation_receipts(
    breakdown: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    receipts = breakdown.get("confirmation_receipts") or {}
    for candidate in candidates:
        receipt = receipts.get(str(candidate["candidate_id"])) or {}
        candidate["confirmed_in_current_scope"] = receipt.get("signature") == candidate_signature(
            candidate
        )
        if candidate["confirmed_in_current_scope"]:
            candidate["formal_asset_id"] = receipt["formal_asset_id"]
            candidate["matched_asset_id"] = receipt["formal_asset_id"]


async def review_state(session: AsyncSession, item: CreationSession) -> dict[str, Any]:
    breakdown = dict((item.settings or {}).get("asset_breakdown") or {})
    parent_id = breakdown.get("parent_job_id")
    result = {
        "parent_job_id": parent_id,
        "input_fingerprint": breakdown.get("input_fingerprint", ""),
        "settled_episode_numbers": [],
        "confirmable_candidate_ids": [],
        "candidate_signatures": {},
        "scopes": [],
    }
    if int(breakdown.get("asset_extraction_contract_version", 1)) not in {2, 3} or not parent_id:
        return result
    parent = await session.get(Job, parent_id, populate_existing=True)
    if parent is None or parent.target_id != item.id:
        return result
    active_keys = set(breakdown.get("scope_order") or [])
    completed = set(breakdown.get("batches") or {})
    scopes: dict[str, dict[str, Any]] = {}
    children = await session.scalars(
        select(Job).where(Job.parent_job_id == parent_id).order_by(Job.id)
    )
    for child in children:
        params = (child.payload or {}).get("parameters") or {}
        key = params.get("scope_key")
        if key not in active_keys or (child.resolution or {}).get("status") in {
            "superseded",
            "replacement_pending",
        }:
            continue
        scopes[key] = {
            "scope_key": key,
            "job_id": child.id,
            "episode_numbers": params.get("episode_numbers") or [],
            "group": params.get("requirement_group"),
            "status": "succeeded" if key in completed else child.status,
        }
    for unit in (parent.payload or {}).get("pending_audio_units") or []:
        key = unit.get("scope_key")
        if key in active_keys and key not in scopes:
            scopes[key] = {
                "scope_key": key,
                "job_id": None,
                "episode_numbers": unit.get("episode_numbers") or [],
                "group": "audio",
                "status": "waiting",
            }
    result["scopes"] = list(scopes.values())
    # Missing scope metadata must fail closed, not turn an unknown range into a success.
    if set(scopes) != active_keys or not active_keys:
        return result
    all_numbers = {number for scope in scopes.values() for number in scope["episode_numbers"]}
    unsettled = {
        number
        for key, scope in scopes.items()
        if key not in completed
        for number in scope["episode_numbers"]
    }
    settled = all_numbers - unsettled
    result["settled_episode_numbers"] = sorted(settled)
    if breakdown.get("status") not in {"running", "failed", "awaiting_confirmation"}:
        return result
    for candidate in breakdown.get("candidates") or []:
        from app.services.creation_finalize_service import _candidate_requires_review

        numbers = set(candidate.get("episode_numbers") or [])
        result["candidate_signatures"][str(candidate["candidate_id"])] = candidate_signature(
            candidate
        )
        if (
            candidate.get("selected", True)
            and not candidate.get("merged_into_candidate_id")
            and not _candidate_requires_review(candidate)
            and candidate.get("readiness_status") != "source_review"
            and numbers
            and numbers <= settled
            and not candidate.get("confirmed_in_current_scope")
        ):
            result["confirmable_candidate_ids"].append(candidate["candidate_id"])
    return result


async def confirm_completed_candidates(
    session: AsyncSession,
    item: CreationSession,
    *,
    candidate_ids: list[int],
    expected_parent_job_id: int,
    input_fingerprint: str,
    expected_signatures: dict[str, str],
) -> dict[str, int]:
    from app.services.creation_breakdown_confirm import confirm_script_asset_breakdown

    await lock_breakdown_session(session, item)
    state = await review_state(session, item)
    if (
        state["parent_job_id"] != expected_parent_job_id
        or state["input_fingerprint"] != input_fingerprint
    ):
        raise ConflictError("资产提取任务或正文版本已变化，请刷新后核对；未调用模型")
    settings = copy.deepcopy(item.settings)
    original = settings["asset_breakdown"]
    from app.services.script_source_snapshot import script_snapshot_is_current

    if not await script_snapshot_is_current(session, item.project_id, input_fingerprint):
        raise ConflictError("正文版本已变化，请重新提取；未写入资产库")
    receipts = dict(original.get("confirmation_receipts") or {})
    requested = set(candidate_ids)
    current = {row["candidate_id"]: row for row in original.get("candidates") or []}
    if any(
        cid not in current or expected_signatures.get(str(cid)) != candidate_signature(current[cid])
        for cid in requested
    ):
        raise ConflictError("候选内容已变化，请刷新并重新核对后确认；未写入资产库")
    already = {
        cid
        for cid in requested
        if cid in current
        and (receipts.get(str(cid)) or {}).get("signature") == candidate_signature(current[cid])
    }
    pending = requested - already
    if not requested or not pending <= set(state["confirmable_candidate_ids"]):
        raise ConflictError("选中范围尚未完成、需要核对或已失效，未写入资产库")
    if not pending:
        return {}
    selected = [copy.deepcopy(current[cid]) for cid in sorted(pending)]
    numbers = sorted({number for row in selected for number in row["episode_numbers"]})
    from app.services import asset_service
    from app.services.creation_finalize_assets import _character_alias_issues

    issues = [
        *(original.get("validation_issues") or []),
        *_character_alias_issues(
            list(current.values()), await asset_service.list_assets(session, item.project_id)
        ),
    ]
    relevant_issues = [
        issue
        for issue in issues
        if issue.get("candidate_id") in pending
        or (set(issue.get("episode_numbers") or []) & set(numbers))
        or (issue.get("candidate_id") is None and not issue.get("episode_numbers"))
    ]
    subset_context = copy.deepcopy(original.get("production_context") or {})
    if (original.get("asset_necessity_policy") or {}).get("candidate_review"):
        # Validate this explicit subset against its own identities, retaining
        # unrelated missing-character obligations on the original breakdown.
        names = {str(name).strip().casefold() for row in selected for name in [row.get("name"), *(row.get("aliases") or [])] if name}
        subset_context["character_appearances"] = [row for row in subset_context.get("character_appearances", [])
                                                   if str(row.get("character_name") or "").strip().casefold() in names]
        relevant_issues = [issue for issue in relevant_issues
                           if issue.get("candidate_id") in pending or issue.get("code") not in {
                               "dialogue_character_missing", "missing_core_character", "core_character_without_episodes",
                               "core_character_episode_gap", "character_alias_conflict", "asset_episode_scope_reduced"}]
    temporary = {
        **copy.deepcopy(original),
        "status": "awaiting_confirmation",
        "scope_mode": "partial",
        "candidates": selected,
        "requested_episode_numbers": numbers,
        "validation_issues": relevant_issues,
        "partial_created_counts": {},
        "partial_created_asset_ids": [],
        # This endpoint confirms an explicit, signed subset; unresolved items
        # remain on the original breakdown restored after the transaction.
        "review_items_by_scope": {},
        "production_context": subset_context,
    }
    item.settings = {**settings, "asset_breakdown": temporary}
    counts = await confirm_script_asset_breakdown(session, item, _locked=True)
    confirmed = item.settings["asset_breakdown"]
    now = datetime.now(UTC).isoformat()
    created_ids = set(original.get("partial_created_asset_ids") or [])
    for candidate in confirmed["candidates"]:
        cid = candidate["candidate_id"]
        if not current[cid].get("matched_asset_id"):
            created_ids.add(candidate["formal_asset_id"])
        current[cid].update(
            matched_asset_id=candidate["formal_asset_id"],
            formal_asset_id=candidate["formal_asset_id"],
            confirmed_in_current_scope=True,
        )
        receipts[str(cid)] = {
            "signature": candidate_signature(current[cid]),
            "formal_asset_id": candidate["formal_asset_id"],
            "confirmed_at": now,
        }
    original["confirmation_receipts"] = receipts
    original["partial_created_asset_ids"] = sorted(created_ids)
    original["partial_created_counts"] = {
        key: int((original.get("partial_created_counts") or {}).get(key, 0)) + value
        for key, value in counts.items()
    }
    original["candidates"] = list(current.values())
    from app.services.asset_admission_identity import bind_admitted_identities
    await bind_admitted_identities(session, item.project_id, [row for row in original["candidates"]
                                                            if row.get("confirmed_in_current_scope")])
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return counts


async def resolve_evidence(session, item, candidate_id, payload, user_id):
    from app.services.creation_breakdown_review import (
        _asset_breakdown_for_review,
        _refresh_candidate_review_state,
    )

    await lock_breakdown_session(session, item)
    await _asset_breakdown_for_review(session, item, _locked=True)
    settings = copy.deepcopy(item.settings)
    breakdown = settings["asset_breakdown"]
    candidate = next(
        (row for row in breakdown.get("candidates", []) if row["candidate_id"] == candidate_id),
        None,
    )
    if (
        breakdown.get("asset_extraction_contract_version") not in {2, 3}
        or breakdown.get("parent_job_id") != payload.expected_parent_job_id
        or breakdown.get("input_fingerprint") != payload.input_fingerprint
        or candidate is None
        or candidate.get("merged_into_candidate_id")
        or candidate.get("confirmed_in_current_scope")
        or candidate_signature(candidate) != payload.expected_signature
    ):
        raise ConflictError("候选或正文已变化，请刷新后核对；未调用模型")
    usages = candidate.get("usage_records") or []
    if payload.usage_index >= len(usages):
        raise ConflictError("原文依据已变化，请刷新后核对")
    usage = usages[payload.usage_index]
    choice = next(
        (
            row
            for row in usage.get("alignment_candidates", [])
            if row["start"] == payload.start and row["end"] == payload.end
        ),
        None,
    )
    if usage.get("alignment_status") != "ambiguous" or choice is None:
        raise ConflictError("只能选择原文中已有的位置，不允许伪造引文")
    usage.update({key: choice[key] for key in ("start", "end", "line_start", "line_end")})
    usage.update(
        alignment_status="exact",
        needs_review=False,
        evidence_resolution="user_selected",
        reviewed_by=user_id,
        reviewed_at=datetime.now(UTC).isoformat(),
    )
    attrs = candidate["attributes"]
    # Never clear unrelated fact/identity/link reviews or legacy unclassified flags.
    if "review_reasons" in attrs:
        reasons = set(attrs["review_reasons"])
        if not any(row.get("needs_review") for row in usages):
            reasons.discard("occurrence_alignment")
        attrs["review_reasons"] = sorted(reasons)
        attrs["needs_review"] = bool(
            reasons
            or attrs.get("identity_fact_conflicts")
            or attrs.get("fact_conflicts")
            or attrs.get("identity_match_status") == "needs_review"
        )
    _refresh_candidate_review_state(candidate)
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return candidate
