"""Field-level production projections retain evidence without promoting guesses."""

# ruff: noqa: RUF001

import re
from copy import deepcopy
from typing import get_args

from app.schemas.asset_extraction import FactField
from app.services.asset_appearance_facts import merge_complementary_appearance

VERSION = "asset-field-trust.ar2.v1"
FIELDS = set(get_args(FactField))
SCOPED = {"costume", "hair", "makeup", "injury", "stage", "story_state", "time", "weather", "lighting", "atmosphere"}
IDENTITY = {"character_name", "owner", "base_scene_name", "gender"}
FIELD_REASONS = {"fact_grounding", "fact_ownership", "fact_conflict", "default_appearance_conflict", "default_appearance", "scoped_appearance"}
LOCAL_VARIANT_REASON = "造型或状态信息不完整，不能自动采用其他变体"


def _confirmed(row, fact, context):
    name = (row.get("attributes") or {}).get("character_identity", {}).get("canonical_name", row["name"])
    people = ((context.get("confirmed_story_bible") or {}).get("content") or {}).get("characters") or []
    matches = [person for person in people if person.get("name") == name]
    return len(matches) == 1 and matches[0].get(fact["field"]) == fact["value"]


def _combine(values, field, kind):
    if len(values) == 1:
        return values[0]
    longest = max(values, key=len)
    if all(value in longest for value in values) and not re.search(r"不是|并非|没有|不穿|未穿", longest):
        return longest
    return merge_complementary_appearance(values, kind=kind) if field == "appearance" else None


def _source_location(fact):
    return (fact.get("source_segment_id"), fact.get("source_unit_id") or (fact.get("start"), fact.get("end")))


def _affirmed(fact):
    quote, value = fact.get("evidence_text") or "", fact.get("value") or ""
    position = quote.find(value) if value else -1
    if position < 0:
        return False
    return not re.search(r"(?:没有|并无|并非|不是|不穿|未穿|不戴|未戴|不曾|未曾|不到|不足|不满)[^，。；！？]{0,6}$", quote[:position])


def organize_candidate(row, context):
    attrs = row.get("attributes") or {}
    if attrs.get("extraction_contract_version") != 3 or row.get("merged_into_candidate_id") or not row.get("selected", True):
        return
    if row.get("asset_type") == "voice" and not attrs.get("facts") and not attrs.get("review_reasons"):
        return
    attrs = deepcopy(attrs)
    facts = attrs.get("facts") or []
    original = attrs.setdefault("field_trust_original", {
        "review_reasons": list(attrs.get("review_reasons") or []),
        "fact_conflicts": list(attrs.get("fact_conflicts") or []),
        "identity_fact_conflicts": deepcopy(attrs.get("identity_fact_conflicts") or {}),
        "identity_match_reason": attrs.get("identity_match_reason"),
    })
    fields = {fact["field"] for fact in facts} | set(original["fact_conflicts"]) | set(original["identity_fact_conflicts"])
    decisions = {}
    for review in attrs.get("manual_production_reviews") or []:
        decisions.update(review.get("field_values") or {})
    projection, statuses, blocking = {}, {}, {}
    for field in sorted(fields):
        records = [(index, fact) for index, fact in enumerate(facts) if fact["field"] == field]
        trusted = [(index, fact) for index, fact in records if (
            fact.get("origin") == "source" and fact.get("quote_verified") and not fact.get("needs_review") and _affirmed(fact)
        ) or fact.get("ownership_status") == "user_confirmed" or (
            fact.get("origin") == "identity_reference" and _confirmed(row, fact, context))]
        values = sorted({fact["value"] for _, fact in trusted})
        uncertain = [index for index, _ in records if index not in {i for i, _ in trusted}]
        status = {"status": "isolated", "fact_indexes": [index for index, _ in trusted], "isolated_fact_indexes": uncertain}
        status["basis"] = sorted({"confirmed_setting" if fact.get("origin") == "identity_reference" else
                                  "user_confirmed" if fact.get("ownership_status") == "user_confirmed" else "source"
                                  for _, fact in trusted})
        if field in decisions:
            projection[field] = decisions[field]
            status.update(status="user_confirmed", value=decisions[field])
        elif values:
            kind = row.get("requirement_type", row.get("asset_type"))
            combined = _combine(values, field, kind)
            if kind == "costume" and field == "stage" and len(values) > 1:
                from app.services.asset_appearance_states import merge_stage_facts

                combined = merge_stage_facts([fact for _, fact in trusted])
            scoped = ((kind == "character" and (field in SCOPED and (len(values) > 1 or field == "injury")))
                      or any(f.get("scope") == "occurrence" for _, f in trusted)
                      or (field in {"time", "weather", "lighting", "atmosphere", "story_state"} and len(values) > 1))
            if field == "story_state" and kind == "prop":
                usage_locations = {_source_location(usage) for usage in row.get("usage_records") or []}
                fact_locations = {_source_location(fact) for _, fact in trusted}
                scoped = scoped or not usage_locations <= fact_locations
            if field == "base_scene_name" and row.get("asset_type") == "scene" and len(values) > 1:
                from app.services.asset_identity_variants import scene_label

                base, _ = scene_label(row["name"])
                if all(value == base or (value.startswith(base) and re.fullmatch(
                    r"(?:中央|中心|[东西南北]侧|.{1,8}(?:前|旁|下|上|内|外))", value[len(base):])) for value in values):
                    combined = base
            if field == "material" and any(re.search(r"光|芒|辉|雾", value) for value in values):
                combined = None
            if scoped:
                status.update(status="scoped", values=values,
                              scopes=[{"fact_index": index, "source_segment_id": fact.get("source_segment_id"),
                                       "source_unit_id": fact.get("source_unit_id"), "value": fact["value"]}
                                      for index, fact in trusted])
            elif combined is not None:
                projection[field] = combined
                status.update(status="verified", value=combined)
            elif field in IDENTITY:
                blocking[field] = values
                status.update(status="conflict", values=values)
            else:
                status.update(status="isolated", values=values)
            if status["status"] == "scoped" and len(values) > 1:
                locations = {}
                for _, fact in trusted:
                    locations.setdefault(_source_location(fact), set()).add(fact["value"])
                if any(len(observed) > 1 for observed in locations.values()):
                    status.update(status="isolated")
        if (field == "character_name" and row.get("asset_type") == "costume"
                and attrs.get("costume_mode") != "garment_only" and field not in projection):
            blocking[field] = values or [fact["value"] for _, fact in records]
            status["status"] = "conflict"
        statuses[field] = status
        attrs.pop(field, None)
    attrs.update(projection)
    reasons = set(attrs.get("review_reasons") or []) - FIELD_REASONS - {"identity_fact_conflict"}
    if (attrs.get("identity_match_reason") == LOCAL_VARIANT_REASON
            and not attrs.get("identity_options") and attrs.get("extraction_identity")):
        attrs["representation_scope_status"] = "independent_source_scope"
        attrs["identity_match_status"] = "source_local"
        attrs.pop("identity_match_reason", None)
        reasons.discard("identity_resolution")
    if blocking:
        reasons.add("identity_fact_conflict")
    attrs["identity_fact_conflicts"] = blocking
    attrs["fact_conflicts"] = sorted(blocking)
    attrs["review_reasons"] = sorted(reasons)
    attrs["needs_review"] = bool(reasons or attrs.get("identity_match_status") == "needs_review")
    attrs["field_trust"] = {"version": VERSION, "fields": statuses,
                            "blocked_fields": sorted(field for field, status in statuses.items() if status["status"] in {"isolated", "conflict", "scoped"})}
    attrs["production_fields"] = projection
    attrs["generation_description"] = (row["name"] + ("；" + "；".join(sorted(set(projection.values()))) if projection else ""))[:1800]
    from app.services.asset_segment_projection import effective_attributes
    attrs = effective_attributes(attrs, {})
    if attrs.get("local_appearance_facts") and attrs.get("generation_description_source") != "user":
        attrs["generation_description"] = (row["name"] + "；" + attrs["generation_description"])[:1800]
    row["attributes"] = attrs


def organize_candidates(candidates, context):
    for row in candidates:
        organize_candidate(row, context)


def prompt_snapshot(snapshot):
    attrs = snapshot.get("attributes") or {}
    if not attrs.get("field_trust"):
        return snapshot
    result = deepcopy(snapshot)
    fields = deepcopy(attrs.get("production_fields") or {})
    overrides = set(snapshot.get("field_overrides") or [])
    manual_profile = snapshot.get("source_profile") or snapshot.get("profile") or {}
    for field in overrides:
        if manual_profile.get(field) in (None, ""):
            fields.pop(field, None)
        else:
            fields[field] = manual_profile[field]
    result["attributes"] = fields
    if attrs.get("generation_description_source") != "user":
        result["description"] = attrs.get("generation_description") or snapshot.get("name", "")
        if overrides:
            result["description"] = (manual_profile.get("description") if "description" in overrides else None) or (
                snapshot.get("name", "") + "；" + "；".join(str(value) for value in fields.values() if isinstance(value, (str, int))))
    blocked = set(attrs["field_trust"].get("blocked_fields") or []) - overrides
    for key in ("profile", "source_profile"):
        profile = result.get(key) or {}
        result[key] = {field: value for field, value in profile.items() if field not in blocked and field not in {
            "facts", "field_trust_original", "field_trust", "character_alias_claims"}
            and (field != "description" or field in overrides)}
    if attrs.get("generation_prompt_source") != "user" and not snapshot.get("prompt_override"):
        result["current_prompt"] = ""
    return result


def generation_text(candidate):
    attrs = candidate.get("attributes") or {}
    description = candidate.get("description") or ""
    if attrs.get("field_trust") and attrs.get("generation_description_source") != "user":
        description = attrs.get("generation_description") or candidate["name"]
    anchor = candidate.get("prompt_anchor") or description
    if attrs.get("field_trust") and attrs.get("generation_prompt_source") != "user":
        anchor = description
    return description, anchor
