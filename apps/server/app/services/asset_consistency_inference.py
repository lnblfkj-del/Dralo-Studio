"""Infer only source-backed relationships; similar names are suggestions."""

import re

VERSION = "asset-consistency.ac1.v1"
VISUAL_TYPES = {"scene", "prop", "character", "costume"}
STATE_FIELDS = {"variant_label", "time", "time_of_day", "weather", "lighting", "injury", "story_state", "stage"}


def normalized(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def source_field(row, key):
    attrs, data = row["attributes"], row["data"]
    if key in (data.get("field_trust_overrides") or []):
        return (data.get("profile") or {}).get(key)
    return (attrs.get("production_fields") or {}).get(key) if attrs.get("field_trust") else attrs.get(key)


def scope_key(row):
    attrs = row["attributes"]
    scope = row["data"].get("script_scope") or attrs
    units = tuple(sorted(str(value) for value in scope.get("unit_ids") or []))
    if units:
        return ("unit", units)
    if scope.get("narrative_structure", "continuous") != "continuous":
        episodes = tuple(sorted(scope.get("episode_numbers") or attrs.get("episode_numbers") or []))
        return ("episode", episodes) if episodes else ("unknown", row["asset_id"])
    identity = attrs.get("character_identity") or {}
    if identity.get("scope") == "source":
        return ("source", identity.get("entity_key") or row["asset_id"])
    return ("project",)


def _base_name(row):
    attrs = row["attributes"]
    if row["asset_type"] == "scene":
        if attrs.get("field_trust") and "base_scene_name" not in (attrs.get("production_fields") or {}):
            return ""
        base = normalized(source_field(row, "base_scene_name"))
        return (base, normalized(source_field(row, "location")), normalized(source_field(row, "owner"))) if base else ""
    if row["asset_type"] == "prop":
        state = str(source_field(row, "story_state") or "")
        name = str(row["name"])
        if state and name.endswith("·" + state):
            name = name[:-(len(state) + 1)]
        return (normalized(name), normalized(source_field(row, "owner")))
    identity = attrs.get("character_identity") or {}
    return str(identity.get("entity_key") or "") if row["asset_type"] == "character" else ""


def source_scenes(row):
    attrs, scope = row["attributes"], row["data"].get("script_scope") or {}
    return {str(item["consistency_scene_key"]) for item in scope.get("usage_records", attrs.get("usage_records", []))
            if item.get("consistency_scene_key") and not item.get("needs_review")
            and item.get("alignment_status") == "exact"}


def base_rank(row):
    # Ranking does not establish facts. Keep state-labelled cards out of the
    # baseline even when those labels have not yet been source-verified.
    def value(key):
        return source_field(row, key) or row["attributes"].get(key)
    return (bool(value("story_state")),
            normalized(value("variant_label")) not in {"", "日", "白天", "day", "daytime"},
            normalized(value("weather")) not in {"", "晴", "晴天", "clear"},
            normalized(value("time")) not in {"", "日", "白天", "day", "daytime"},
            not bool(value("time")), row["asset_id"])


def infer(rows):
    by_id = {row["asset_id"]: row for row in rows}
    scopes = {row["asset_id"]: scope_key(row) for row in rows}
    scene_keys = {row["asset_id"]: source_scenes(row) for row in rows}
    props_at_scene = {}
    for row in rows:
        if row["asset_type"] == "prop":
            for scene in scene_keys[row["asset_id"]]:
                props_at_scene.setdefault((scopes[row["asset_id"]], scene), set()).add(row["asset_id"])
    groups = {}
    for row in rows:
        base = _base_name(row)
        if base:
            groups.setdefault((row["asset_type"], scopes[row["asset_id"]], base), []).append(row)
    result = {}
    for row in rows:
        asset_id, attrs = row["asset_id"], row["attributes"]
        relation = {"identity_asset_id": None, "location_asset_id": None, "kind": "base", "props": [],
                    "design_description": "", "origin": "source", "issues": []}
        group = groups.get((row["asset_type"], scopes[asset_id], _base_name(row)), [])
        # A neutral/day state is a practical first design, not a claim that other
        # story states disappear. Source identities and cards remain separate.
        if len(group) > 1:
            root = min(group, key=base_rank)
            if root["asset_id"] != asset_id:
                relation.update(identity_asset_id=root["asset_id"], kind="state")
        if row["asset_type"] == "character":
            explicit = (attrs.get("character_identity") or {}).get("base_asset_id")
            if explicit in by_id and explicit != asset_id and scope_key(row) == scope_key(by_id[explicit]):
                relation.update(identity_asset_id=explicit, kind="state")
        if row["asset_type"] == "scene":
            location = normalized(source_field(row, "location"))
            matches = [other for other in rows if other["asset_type"] == "scene"
                       and other["asset_id"] != asset_id and scope_key(other) == scope_key(row)
                       and not (_base_name(row) and _base_name(other) == _base_name(row))
                       and location and normalized(other["name"]) == location]
            if len(matches) == 1:
                relation["location_asset_id"] = matches[0]["asset_id"]
            scenes = scene_keys[asset_id]
            props = {prop for scene in scenes for prop in props_at_scene.get((scopes[asset_id], scene), set())}
            for prop in sorted(props):
                relation["props"].append({"asset_id": prop, "presence": "occurrence",
                                          "source_scene_keys": sorted(scenes & scene_keys[prop])})
        manual = row["data"].get("consistency")
        if isinstance(manual, dict) and manual.get("version") == VERSION:
            relation.update({key: manual[key] for key in relation if key in manual and key not in {"origin", "issues"}})
            relation["origin"] = "manual"
        for key in ("identity_asset_id", "location_asset_id"):
            target = relation[key]
            if target is not None and target not in by_id:
                relation["issues"].append("关联资产已归档或移出项目")
                relation[key] = None
                if key == "identity_asset_id":
                    relation["kind"] = "base"
        relation["props"] = [item for item in relation["props"] if item["asset_id"] in by_id]
        result[asset_id] = relation
    return result
