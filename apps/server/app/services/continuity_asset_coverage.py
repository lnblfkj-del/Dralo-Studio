"""Resolve source-backed continuity requirements against current project assets."""

from copy import deepcopy
import re


def default_costume_covers(character, value, *, initial_evidence=None):
    """Accept a source-backed elaboration of the character's existing default outfit."""
    def compact(text):
        return re.sub(r"[\s\u3002\uff0c,;\uff1b:：]+", "", str(text or ""))

    costume = compact((character.get("profile") or {}).get("costume"))
    requested = compact(value)
    if not costume and initial_evidence:
        # Scoped descriptions can omit the global costume field. The first
        # source-backed outfit is carried by the character reference, not a new change.
        choices = {compact(fact.get("value")) for fact in character.get("facts", [])
                   if fact.get("field") == "costume" and fact.get("quote_verified")
                   and not fact.get("needs_review") and compact(fact.get("value"))
                   and compact(fact.get("value")) in requested
                   and compact(initial_evidence) in compact(fact.get("evidence_text"))}
        if len(choices) == 1:
            costume = choices.pop()
    if not costume or not requested:
        return False
    if requested == costume:
        return True
    if costume not in requested:
        return False
    extra = requested.replace(costume, "", 1)
    if re.search(r"换穿|换上|换成|脱下|改穿|破损|撕裂|破裂|染血|血污|烧毁|烧焦|湿透", extra):
        return False
    # Accessories in the same original description do not require another outfit image.
    clauses = [compact(part) for part in re.split(r"[，,;；。]", value) if compact(part)]
    return any(fact.get("field") == "costume" and fact.get("quote_verified")
               and not fact.get("needs_review") and compact(fact.get("value")) == costume
               and all(clause in compact(fact.get("evidence_text")) for clause in clauses)
               for fact in character.get("facts", []))


def character_ids(asset):
    profile = asset.get("profile") or {}
    return {value for value in (
        profile.get("character_asset_id"), profile.get("linked_character_asset_id"),
        *(profile.get("linked_character_asset_ids") or []),
    ) if type(value) is int and value > 0}


def cover_assets(assets, refs, plan, segment_key):
    from app.services.segment_asset_references import reference

    result = deepcopy(refs)
    if plan.continuity is None:
        return result
    segment = next((item for item in plan.segments if item.key == segment_key), None)
    if segment is None:
        return result
    wanted = {shot.key for shot in segment.shots}
    catalog = {item["asset_id"]: item for item in assets}
    identities = {item.key: item for item in plan.continuity.identities}
    selected = {item["asset_id"]: item for item in result.get("asset_references", [])}
    pinned = {item["asset_id"] for item in result.get("asset_bindings", [])}
    sources = [key for shot in segment.shots for key in shot.source_keys]
    state, costumes, touched, initial_costumes = {}, {}, set(), {}
    visible_names = {identity.name for identity in identities.values() if identity.asset_id in selected}
    for beat in plan.continuity.beats:
        if beat.block_key in wanted:
            for (entity, field), value in state.items():
                if field == "costume":
                    costumes.setdefault(entity, set()).add(value)
                if field == "holder" and value in visible_names:
                    touched.add(entity)
        for change in beat.changes:
            if change.field == "costume":
                initial_costumes.setdefault(change.entity_key, (change.after, change.evidence))
            state[(change.entity_key, change.field)] = change.after
            if beat.block_key in wanted:
                touched.add(change.entity_key)
                if change.field == "costume":
                    costumes.setdefault(change.entity_key, set()).add(change.after)
        if beat.block_key == segment.shots[-1].key:
            break
    issues, required = [], set()

    def problem(code, name, message):
        issues.append({"code": code, "name": name, "message": message})

    def include(asset, *, fixed=False):
        selected.setdefault(asset["asset_id"], reference(asset, source_keys=sources))
        if fixed:
            required.add(asset["asset_id"])

    # Only entities participating in this segment are visual requirements.
    for key in sorted(touched):
        identity = identities[key]
        if identity.kind not in {"character", "scene", "prop", "costume"}:
            continue
        choices = ([catalog[identity.asset_id]] if identity.asset_id in catalog else
                   [asset for asset in assets if identity.name in {asset["asset_name"], *asset.get("aliases", [])}
                    and asset["asset_type"] == identity.kind])
        manual = {item.get("name"): item.get("asset_id") for item in result.get("manual_asset_matches", [])}
        if manual.get(identity.name) in catalog:
            candidate = catalog[manual[identity.name]]
            if candidate["asset_type"] == identity.kind:
                choices = [candidate]
        if len(choices) == 1:
            include(choices[0], fixed=True)
        else:
            problem("asset_missing" if not choices else "asset_ambiguous", identity.name,
                    f"{identity.name}：{'缺少对应资产，请补建或关联' if not choices else '存在多个同名资产，请明确关联'}")
    for key, values in costumes.items():
        identity = identities[key]
        if identity.asset_id not in selected:
            continue
        character = catalog.get(identity.asset_id)
        if not character:
            continue
        variants = [asset for asset in assets if asset["asset_type"] == "costume"
                    and identity.asset_id in character_ids(asset)]
        explicit = [asset for asset in variants if asset["asset_id"] in pinned]
        if explicit:
            for asset in explicit:
                include(asset, fixed=True)
            for asset in variants:
                if asset["asset_id"] not in pinned:
                    selected.pop(asset["asset_id"], None)
            continue
        chosen = set()
        for value in sorted(values):
            matches = []
            for asset in variants:
                labels = {asset["asset_name"], *(asset.get("aliases") or []),
                          (asset.get("profile") or {}).get("costume")}
                labels.update(label.removeprefix(identity.name).strip(" ·-")
                              for label in list(labels) if isinstance(label, str))
                if value in labels:
                    matches.append(asset)
            if len(matches) == 1:
                include(matches[0], fixed=True)
                chosen.add(matches[0]["asset_id"])
            elif not default_costume_covers(character, value, initial_evidence=(
                initial_costumes[key][1] if key in initial_costumes and initial_costumes[key][0] == value else None
            )):
                problem("costume_missing" if not matches else "costume_ambiguous", identity.name,
                        f"{identity.name}：造型“{value}”{'尚无唯一匹配，请在资产库补齐或手动指定造型版本' if not matches else '匹配到多个资产，请手动指定造型版本'}")
        # Remove automatically selected alternative outfits, never user-pinned choices.
        if chosen:
            for asset in variants:
                if asset["asset_id"] not in chosen and asset["asset_id"] not in pinned:
                    selected.pop(asset["asset_id"], None)
    result["asset_references"] = list(selected.values())
    result["coverage_issues"] = issues
    required.update(item["asset_id"] for item in selected.values()
                    if item["asset_type"] in {"character", "scene", "costume"})
    result["continuity_required_asset_ids"] = sorted(required & selected.keys())
    return result


async def apply_coverage(session, segment, assets, refs):
    from app.models import Episode, EpisodeProductionPlan
    from app.models.episode_planning import EpisodePlanningRecord
    from app.services.episode_planning_storage import read_plan

    if not getattr(segment, "plan_id", None):
        return refs
    production = await session.get(EpisodeProductionPlan, segment.plan_id)
    record_id = (production.parameters or {}).get("planning_record_id") if production else None
    if not record_id:
        return refs
    record = await session.get(EpisodePlanningRecord, record_id)
    episode = await session.get(Episode, segment.episode_id)
    if record is None or record.episode_id != segment.episode_id:
        return refs
    plan = read_plan(record)
    if plan.script_revision != episode.script_revision:
        return refs
    return cover_assets(assets, refs, plan, segment.lineage_key)
