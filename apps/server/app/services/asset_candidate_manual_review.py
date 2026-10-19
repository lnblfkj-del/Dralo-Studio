"""Record explicit production decisions while retaining original source assertions."""

from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select

from app.core.errors import ConflictError
from app.services.asset_extraction_review import candidate_signature
from app.services.asset_extraction_scheduling import lock_breakdown_session

FIELDS = {"age", "appearance", "costume", "hair", "makeup", "injury", "stage", "character_name",
          "base_scene_name", "variant_label", "time", "weather", "lighting", "owner", "story_state",
          "personality", "voice", "material", "color"}


def apply_decision(candidate, payload, actor_id, *, context=None):
    attrs = candidate.setdefault("attributes", {})
    facts = attrs.get("facts") or []
    conflicts = set(attrs.get("fact_conflicts") or [])
    identity_conflicts = attrs.get("identity_fact_conflicts") or {}
    allowed_fields = (conflicts | set(identity_conflicts)) & FIELDS
    independent = payload.keep_independent
    if independent and not candidate.get("usage_records"):
        raise ConflictError("缺少原文出处，请先补充来源或在资产库手工新建")
    if not payload.accepted_fact_indexes and not payload.field_values and not independent and not payload.confirm_usage_relation:
        raise ConflictError("请选择需核对的依据或填写固定采用值")
    if not set(payload.field_values) <= allowed_fields:
        raise ConflictError("只能修改本候选待核对的冲突字段")
    original = deepcopy({key: attrs.get(key) for key in (
        "facts", "fact_conflicts", "identity_fact_conflicts", "review_reasons",
        "identity_options", "identity_name_options", "identity_match_status", "voice_source")})
    for index in payload.accepted_fact_indexes:
        if index >= len(facts):
            raise ConflictError("依据已变化，请刷新")
        fact = facts[index]
        if (not fact.get("needs_review") or fact.get("origin") != "source"
                or fact.get("alignment_status") != "exact" or not fact.get("evidence_text")):
            raise ConflictError("该依据不能直接确认，请先定位原文或处理其他核对问题")
        fact.update(needs_review=False, review_resolution="user_confirmed_attribution", ownership_status="user_confirmed")
        field = fact.get("field")
        if field in FIELDS and not attrs.get(field):
            attrs[field] = fact.get("value")
    for field, value in payload.field_values.items():
        attrs[field] = value
        conflicts.discard(field)
        identity_conflicts.pop(field, None)
    reasons = set(attrs.get("review_reasons") or [])
    if payload.confirm_usage_relation:
        if "necessity_relation" not in reasons:
            raise ConflictError("当前没有待确认的用途关联")
        reasons.discard("necessity_relation")
        attrs["necessity_relation_resolution"] = "user_confirmed"
    if independent:
        # A manual identity decision does not certify unlocated quotes or conflicting fields.
        reasons -= {"identity_resolution", "identity_name", "visual_link"}
        for key in ("identity_options", "identity_name_options", "identity_name_status",
                    "identity_name_reason", "identity_match_reason"):
            attrs.pop(key, None)
        attrs["identity_match_status"] = "user_independent"
        attrs["identity_resolution_by"] = "user_independent"
        candidate["matched_asset_id"] = None
        if candidate.get("requirement_type") == "character_voice":
            attrs.pop("linked_asset_key", None)
            attrs["voice_source"] = {"grounding": "user_independent", "speaker": attrs.get("character_name"),
                                     "speaker_kind": "independent"}
    if payload.accepted_fact_indexes:
        if not any(fact.get("needs_review") and fact.get("ownership_status") == "unresolved" for fact in facts):
            reasons.discard("fact_ownership")
        if not any(fact.get("needs_review") for fact in facts):
            reasons.discard("fact_grounding")
    if payload.field_values:
        if not conflicts:
            reasons.discard("fact_conflict")
        if not identity_conflicts:
            reasons.discard("default_appearance_conflict")
    attrs["fact_conflicts"] = sorted(conflicts)
    attrs["identity_fact_conflicts"] = identity_conflicts
    attrs["review_reasons"] = sorted(reasons)
    attrs["needs_review"] = bool(reasons or conflicts or identity_conflicts
        or any(fact.get("needs_review") for fact in facts) or attrs.get("identity_match_status") == "needs_review")
    record = {"actor_id": actor_id, "reviewed_at": datetime.now(UTC).isoformat(),
              "reason": payload.reason, "accepted_fact_indexes": payload.accepted_fact_indexes,
              "field_values": payload.field_values, "keep_independent": independent,
              "confirm_usage_relation": payload.confirm_usage_relation, "original": original}
    attrs.setdefault("manual_production_reviews", []).append(record)
    if attrs.get("field_trust"):
        from app.services.asset_field_trust import organize_candidate

        organize_candidate(candidate, context or {})
    from app.services.creation_breakdown_review import _refresh_candidate_review_state
    _refresh_candidate_review_state(candidate)


async def _resolve_candidate(session, item, breakdown, candidate_id, payload, actor_id):
    candidate = next((row for row in breakdown.get("candidates", []) if row["candidate_id"] == candidate_id), None)
    if (breakdown.get("asset_extraction_contract_version") not in {2, 3}
            or breakdown.get("parent_job_id") != payload.expected_parent_job_id
            or breakdown.get("input_fingerprint") != payload.input_fingerprint
            or candidate is None or candidate.get("merged_into_candidate_id") or candidate.get("confirmed_in_current_scope")):
        raise ConflictError("候选或正文已变化，请刷新后核对；未调用模型")
    receipts = breakdown.setdefault("manual_review_receipts", {})
    key = f"{candidate_id}:{payload.expected_signature}"
    signature = candidate_signature(candidate)
    if key in receipts and receipts[key]["request"] == payload.model_dump() and receipts[key]["after_signature"] == signature:
        return candidate
    if signature != payload.expected_signature:
        raise ConflictError("候选已修改，请刷新后再核对；未调用模型")
    from app.models import Episode
    usages = candidate.get("usage_records") or []
    episodes = {episode.number: episode for episode in await session.scalars(select(Episode).where(
        Episode.project_id == item.project_id, Episode.number.in_([record.get("episode_number") for record in usages]))) }
    if payload.keep_independent or payload.confirm_usage_relation:
        if not usages:
            raise ConflictError("缺少原文出处，请先补充来源")
        for usage in usages:
            episode = episodes.get(usage.get("episode_number"))
            start, end = usage.get("start"), usage.get("end")
            if (not episode or usage.get("needs_review") or usage.get("alignment_status") != "exact"
                    or episode.script_revision != usage.get("script_revision")
                    or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(episode.script or "")
                    or (episode.script or "")[start:end] != usage.get("source_excerpt")):
                raise ConflictError("原文位置或版本仍待核对，请先在原文依据中确认位置")
    facts = (candidate.get("attributes") or {}).get("facts") or []
    for index in payload.accepted_fact_indexes:
        if index >= len(facts):
            raise ConflictError("依据已变化，请刷新")
        fact = facts[index]
        usage = next((record for record in usages if record.get("source_segment_id") == fact.get("source_segment_id")
            and record.get("source_unit_id") == fact.get("source_unit_id")), None)
        episode = episodes.get(usage.get("episode_number")) if usage else None
        start, end = fact.get("start"), fact.get("end")
        if (not episode or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(episode.script or "")
                or (episode.script or "")[start:end] != fact.get("evidence_text")):
            raise ConflictError("核对依据未能定位到当前正文，请先校正原文位置")
    apply_decision(candidate, payload, actor_id, context=breakdown.get("production_context") or {})
    receipts[key] = {"request": payload.model_dump(), "after_signature": candidate_signature(candidate)}
    return candidate


async def resolve_many(session, item, entries, actor_id):
    from app.services.creation_breakdown_review import _asset_breakdown_for_review, _refresh_asset_candidate_counts
    await lock_breakdown_session(session, item)
    await _asset_breakdown_for_review(session, item, _locked=True)
    settings = deepcopy(item.settings)
    breakdown = settings["asset_breakdown"]
    candidates = []
    for candidate_id, payload in entries:
        candidates.append(await _resolve_candidate(session, item, breakdown, candidate_id, payload, actor_id))
    _refresh_asset_candidate_counts(breakdown)
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return candidates


async def resolve_review(session, item, candidate_id, payload, actor_id):
    return (await resolve_many(session, item, [(candidate_id, payload)], actor_id))[0]
