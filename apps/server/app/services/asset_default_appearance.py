"""Complete canonical actor defaults from verified facts, not temporary states."""

from copy import deepcopy

from app.services.asset_scoped_costumes import VISUAL_FIELDS


def complete_defaults(breakdown, *, existing, policy):
    if not (policy or {}).get("appearance_completion"):
        return breakdown
    result = deepcopy(breakdown)
    for actor in result.get("characters") or []:
        attrs = actor["attributes"]
        if not attrs.get("appearance_states"):
            continue
        stable = [fact for fact in attrs.get("facts") or []
                  if fact.get("quote_verified") and fact.get("scope") != "occurrence"
                  and fact.get("field") in VISUAL_FIELDS
                  and fact.get("ownership_status") in {None, "explicit_subject", "local_pronoun"}]
        matches = [row for row in existing if row.get("asset_type") == "character"
                   and row["name"].strip().casefold() == actor["name"].strip().casefold()
                   and not (row.get("attributes") or {}).get("needs_review")]
        # Formal identity is reused only when unique; do not borrow another
        # person's appearance via a guessed alias or copy an entire description.
        if len(matches) == 1:
            stable += [fact for fact in (matches[0].get("attributes") or {}).get("facts") or []
                       if fact.get("quote_verified") and fact.get("scope") != "occurrence"
                       and fact.get("field") in VISUAL_FIELDS
                       and fact.get("ownership_status") in {None, "explicit_subject", "local_pronoun"}]
        selected, conflicts = {}, {}
        for field in VISUAL_FIELDS:
            values = sorted({fact["value"] for fact in stable if fact["field"] == field})
            if len(values) == 1:
                selected[field] = values[0]
            elif len(values) > 1:
                from app.services.asset_appearance_facts import merge_complementary_appearance
                combined = merge_complementary_appearance(values) if field == "appearance" else None
                if combined:
                    selected[field] = combined
                else:
                    conflicts[field] = values
        reasons = set(attrs.get("review_reasons") or []) - {"default_appearance"}
        if conflicts:
            attrs["identity_fact_conflicts"] = {**attrs.get("identity_fact_conflicts", {}), **conflicts}
            reasons.add("default_appearance_conflict")
        attrs.update(selected)
        attrs["default_appearance_status"] = "verified" if selected else "design_pending"
        attrs["default_appearance_sources"] = stable
        attrs["appearance_completion"] = "stable_identity.v1"
        attrs["review_reasons"] = sorted(reasons)
        attrs["needs_review"] = bool(reasons or attrs.get("identity_fact_conflicts"))
        actor["description"] = "；".join(sorted(set(selected.values()))) or actor["name"]
        actor["prompt_anchor"] = ""
    return result
