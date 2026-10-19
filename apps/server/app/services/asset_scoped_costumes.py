"""Project owned, source-local appearance states without changing actor identity."""

import hashlib
import json
import re
from copy import deepcopy

from app.services.creation_breakdown_planning import normalized_asset_key

VISUAL_FIELDS = {"appearance", "costume", "hair", "makeup", "injury", "stage", "story_state"}
SEQUENTIAL = re.compile(r"随后|然后|接着|之后|先.*再|\b(?:then|later|afterwards)\b", re.I)


def location(record):
    return (record.get("source_segment_id"), record.get("source_unit_id") or
            (record.get("start"), record.get("end")))


def _covered(costume, actor_name, states):
    attrs = costume.get("attributes") or {}
    if attrs.get("character_name") != actor_name or attrs.get("needs_review"):
        return False
    facts = [fact for fact in attrs.get("facts") or [] if fact.get("quote_verified")]
    return all(any(location(fact) == location(state) and state["value"] == fact["value"]
                   and state["field"] == fact["field"]
                   for fact in facts if fact.get("field") in VISUAL_FIELDS) for state in states)


def _derived(actor, states, usages):
    values = sorted({state["value"] for state in states})
    stage = "；".join(values)
    # Same explicit appearance can be reused at another occurrence; source
    # positions belong to usage, not to an identity that duplicates each episode.
    signature = hashlib.sha256(json.dumps([actor["name"], values], ensure_ascii=False).encode()).hexdigest()[:10]
    name = f"{actor['name']}·{stage}"
    if len(name) > 100:
        name = f"{name[:80]}-{signature}"
    attrs = {
        "asset_key": normalized_asset_key("costume", name), "extraction_contract_version": 3,
        "character_name": actor["name"], "stage": stage, "facts": deepcopy(states),
        "needs_review": False, "review_reasons": [], "media_delivery": "description",
        "appearance_projection": "scoped_costume.v1",
        "appearance_state_scopes": deepcopy(usages),
        "necessity_records": [{
            "reasons": ["fixed_appearance"], "source_segment_id": state["source_segment_id"],
            "source_unit_id": state.get("source_unit_id", ""), "evidence_text": state["evidence_text"],
            "rationale": "已核实归属的局部造型变化，与角色默认外观分开管理",
            "source_verified": True, "entity_relation": "owned_appearance_state",
            "relation_version": "scoped_costume.v1",
        } for state in states],
    }
    for field in VISUAL_FIELDS - {"stage"}:
        selected = {state["value"] for state in states if state["field"] == field}
        if len(selected) == 1:
            attrs[field] = selected.pop()
    return {"name": name, "aliases": [], "description": f"{actor['name']}：{stage}",
            "prompt_anchor": "", "episode_numbers": sorted({usage["episode_number"] for usage in usages}),
            "attributes": attrs, "usage_records": deepcopy(usages)}


def project_scoped_costumes(breakdown, *, kinds, policy):
    if not (policy or {}).get("appearance_projection") or "costume" not in kinds:
        return breakdown
    result = deepcopy(breakdown)
    result.setdefault("costumes", [])
    for actor in result.get("characters") or []:
        attrs = actor["attributes"]
        states = attrs.get("appearance_states") or []
        if not states:
            continue
        grouped = {}
        for state in states:
            grouped.setdefault(location(state), []).append(state)
        links, unresolved = [], False
        for scope, scoped in grouped.items():
            usages = [usage for usage in actor.get("usage_records") or []
                      if location(usage) == scope and not usage.get("needs_review")]
            fields = {}
            for state in scoped:
                fields.setdefault(state["field"], set()).add(state["value"])
            if (not usages or any(not state.get("quote_verified")
                                  or state.get("ownership_status") not in {"explicit_subject", "local_pronoun"}
                                  or SEQUENTIAL.search(state["evidence_text"]) for state in scoped)
                    or any(len(values) > 1 for values in fields.values())
                    or set(attrs.get("review_reasons") or []) - {"scoped_appearance", "default_appearance"}):
                unresolved = True
                continue
            matches = [row for row in result["costumes"] if _covered(row, actor["name"], scoped)]
            if len(matches) > 1:
                unresolved = True
                continue
            costume = matches[0] if matches else _derived(actor, scoped, usages)
            if not matches:
                result["costumes"].append(costume)
            links.append({"costume_name": costume["name"], "source_segment_id": scope[0],
                          "source_unit_id": scoped[0].get("source_unit_id", ""),
                          "values": sorted({state["value"] for state in scoped})})
        attrs["appearance_state_links"] = links
        # Never leave a temporary state in the generic description used by image
        # prompts. Retain the original for review, rebuild only from stable facts.
        stable = sorted({fact["value"] for fact in attrs.get("facts") or []
                         if fact.get("quote_verified") and fact.get("scope") != "occurrence"
                         and fact.get("field") in VISUAL_FIELDS})
        originals = attrs.setdefault("extraction_descriptions", [])
        if not originals:
            originals.append(actor["description"])
        actor["description"] = "；".join(stable) if stable else actor["name"]
        actor["prompt_anchor"] = ""
        reasons = set(attrs.get("review_reasons") or [])
        if not unresolved and len(links) == len(grouped):
            reasons.discard("scoped_appearance")
        if not stable:
            reasons.add("default_appearance")
        attrs["review_reasons"] = sorted(reasons)
        attrs["needs_review"] = bool(reasons)
    return result


def prompt_identity_snapshot(snapshot):
    """Keep audit/source scopes in storage, not in default actor image instructions."""
    from app.services.asset_field_trust import prompt_snapshot

    snapshot = prompt_snapshot(snapshot)
    attrs = snapshot.get("attributes") or {}
    if snapshot.get("type") != "character" or not attrs.get("appearance_states"):
        return snapshot
    result = deepcopy(snapshot)
    # Only default identity fields are model-facing; evidence quotes can contain
    # a temporary state even when the fact itself describes a stable feature.
    result["attributes"] = {}
    result.pop("source_profile", None)
    return result
