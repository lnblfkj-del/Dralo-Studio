"""Project trusted fields and source-local identities into a single segment."""

import re
from copy import deepcopy

TEMPORARY_APPEARANCE = re.compile(r"受伤|重伤|断腿|断臂|浴血|染血|血污|绷带|流血|伤痕|伤口|满头大汗|鼻青脸肿")


def effective_attributes(attributes, data):
    result = deepcopy({**(attributes or {}), **((data or {}).get("extraction_projection") or {})})
    trust = result.get("field_trust") or {}
    state = (trust.get("fields") or {}).get("appearance") or {}
    facts = result.get("facts") or []
    local = [index for index in state.get("fact_indexes") or [] if state.get("status") == "verified" and 0 <= index < len(facts)
             and TEMPORARY_APPEARANCE.search(str(facts[index].get("value") or ""))]
    if local and "appearance" not in ((data or {}).get("field_trust_overrides") or []):
        result["local_appearance_facts"] = local
        fields = result.setdefault("production_fields", {})
        stable = [str(facts[index]["value"]) for index in state.get("fact_indexes") or []
                  if 0 <= index < len(facts) and index not in local]
        if stable:
            from app.services.asset_appearance_facts import merge_complementary_appearance
            combined = merge_complementary_appearance(stable, kind="character") if len(stable) > 1 else stable[0]
            if combined:
                fields["appearance"] = combined
            else:
                fields.pop("appearance", None)
        else:
            fields.pop("appearance", None)
        if fields.get("appearance"):
            result["appearance"] = fields["appearance"]
        else:
            result.pop("appearance", None)
        if result.get("generation_description_source") != "user":
            result["generation_description"] = "; ".join(str(value) for value in fields.values())
    return result


def production_projection(attributes, data, name, description):
    attributes = effective_attributes(attributes, data)
    profile = {**attributes, **(data.get("profile") or {})}
    if attributes.get("character_identity") and "aliases" not in (data.get("field_trust_overrides") or []):
        profile["aliases"] = list(attributes.get("aliases") or [])
    trust = attributes.get("field_trust") or {}
    if trust:
        overrides = set(data.get("field_trust_overrides") or [])
        for field in trust.get("fields") or {}:
            if field not in overrides:
                profile.pop(field, None)
        profile.update({key: value for key, value in (attributes.get("production_fields") or {}).items()
                        if key not in overrides})
        if attributes.get("generation_description_source") != "user":
            description = name + "; " + "; ".join(str(profile[key]) for key in trust.get("fields", {})
                                                  if profile.get(key))
    return profile, description


def usage_matches(row, episode, units):
    if (row.get("episode_number") != episode.number or row.get("needs_review")
            or row.get("script_revision", episode.script_revision) != episode.script_revision):
        return False
    quote = str(row.get("source_excerpt") or "").strip()
    if not quote or quote not in (episode.script or ""):
        return False
    start, end = row.get("line_start"), row.get("line_end")
    lines = {line for unit in units for line in unit.get("source_lines", [])}
    if type(start) is int and type(end) is int and lines:
        return any(start <= line <= end for line in lines)
    return any(quote in unit["text"] or unit["text"].strip() in quote for unit in units)


def eligible(asset, episode, units=None):
    numbers = asset.get("episode_numbers") or []
    if numbers and episode.number not in numbers:
        return False
    identity = asset.get("character_identity") or {}
    if identity.get("scope") != "source":
        return True
    rows = asset.get("usage_records") or []
    if units is None:
        return any(row.get("episode_number") == episode.number
                   and row.get("script_revision", episode.script_revision) == episode.script_revision
                   and not row.get("needs_review") for row in rows)
    return any(usage_matches(row, episode, units) for row in rows)


def contextual_asset(asset, episode, units):
    result = deepcopy(asset)
    fields = (asset.get("field_trust") or {}).get("fields") or {}
    facts = asset.get("facts") or []
    usage = [row for row in asset.get("usage_records") or [] if usage_matches(row, episode, units)]
    local = {}
    for field, state in fields.items():
        if state.get("status") != "scoped" or field in (asset.get("field_overrides") or []):
            continue
        values = set()
        for scope in state.get("scopes") or []:
            index = scope.get("fact_index")
            if type(index) is not int or not 0 <= index < len(facts):
                continue
            fact = facts[index]
            # Both source identities must agree; a scene-only match is not enough.
            if any(row.get("source_segment_id") == fact.get("source_segment_id")
                   and fact.get("source_unit_id") and row.get("source_unit_id") == fact["source_unit_id"]
                   for row in usage):
                values.add(str(scope["value"]))
        if len(values) == 1:
            local[field] = next(iter(values))
    result["profile"].update(local)
    appearances = {str(facts[index]["value"]) for index in asset.get("local_appearance_facts") or []
                   if 0 <= index < len(facts) and any(row.get("source_segment_id") == facts[index].get("source_segment_id")
                   and facts[index].get("source_unit_id") and row.get("source_unit_id") == facts[index]["source_unit_id"] for row in usage)}
    if appearances and "appearance" not in (asset.get("field_overrides") or []):
        local["appearance"] = "; ".join(sorted(appearances))
        result["profile"]["appearance"] = "; ".join(filter(None, [result["profile"].get("appearance"), local["appearance"]]))
    result["scoped_fields"] = local
    return result


def voice_character_ids(asset, assets):
    identity = asset.get("character_identity") or {}
    key = identity.get("entity_key")
    if not key or identity.get("form", "single") != "single":
        return {asset["asset_id"]}
    return {row["asset_id"] for row in assets if row["asset_type"] == "character"
            and (row.get("character_identity") or {}).get("entity_key") == key
            and (row.get("character_identity") or {}).get("form", "single") == "single"}
