"""Freeze shared design definitions into prompt inputs and detect later edits."""

# ruff: noqa: RUF001

from app.services.asset_consistency_inference import STATE_FIELDS, VERSION, infer
from app.services.asset_consistency_service import fingerprint, project_rows
from app.services.asset_prompt_state import asset_snapshot
from app.services.asset_scoped_costumes import prompt_identity_snapshot


def _definition(row, relation, *, stable=False):
    safe = prompt_identity_snapshot(asset_snapshot(row["asset"], row["link"]))
    fields = {**(safe.get("attributes") or {}), **(safe.get("profile") or {})}
    allowed = {"age", "gender", "appearance", "costume", "hair", "makeup", "material", "environment", "location",
               "base_scene_name", "owner", "description", *STATE_FIELDS}
    fields = {key: value for key, value in fields.items() if key in allowed and value not in (None, "", [], {})
              and (not stable or key not in STATE_FIELDS | {"description"})}
    if row["asset_type"] == "character":
        from app.services.asset_character_representation import character_profile
        fields.update({key: value for key, value in character_profile(row["attributes"], row["data"]).items()
                       if value is not None})
    definition = relation.get("design_description") or row["data"].get("consistency_design") or ""
    # An accepted root prompt is the shared design. State prompts are never
    # promoted to a location's stable definition by an arbitrary latest version.
    manual_prompt = row["data"].get("field_trust_prompt_override") or row["attributes"].get("generation_prompt_source") == "user"
    prompt = row["prompt"] if not definition and manual_prompt and (not stable or relation["kind"] == "base") else ""
    return {"asset_id": row["asset_id"], "name": row["name"], "asset_type": row["asset_type"],
            "fields": fields, "design_description": definition,
            "design_job_id": None if relation.get("design_description") else row["data"].get("consistency_design_job_id"),
            "accepted_prompt": prompt, "description": "" if stable else safe.get("description") or row["description"]}


async def contexts(session, project_id, asset_ids=None):
    rows = await project_rows(session, project_id)
    relations, by_id = infer(rows), {row["asset_id"]: row for row in rows}
    output = {}
    for row in rows:
        if asset_ids is not None and row["asset_id"] not in asset_ids:
            continue
        ident, relation = row["asset_id"], relations[row["asset_id"]]
        dependencies, visited = [], {ident}
        def append(target, role, *, stable=False, presence=None, visited=visited, dependencies=dependencies):
            if target in visited or target not in by_id:
                return
            visited.add(target)
            target_relation = relations[target]
            parent = target_relation.get("identity_asset_id")
            if parent:
                append(parent, role, stable=stable)
                if stable:
                    return
            location = target_relation.get("location_asset_id")
            if location:
                append(location, "location", stable=True)
            dependencies.append({"role": role, "presence": presence,
                                 **_definition(by_id[target], target_relation, stable=stable)})
        if relation.get("identity_asset_id"):
            append(relation["identity_asset_id"], "identity", stable=True)
        if relation.get("location_asset_id"):
            append(relation["location_asset_id"], "location", stable=True)
        # Existing costume-to-character links use the same source of truth.
        if row["asset_type"] == "costume":
            attrs, profile = row["attributes"], row["data"].get("profile") or {}
            if profile.get("costume_mode", attrs.get("costume_mode")) != "garment_only":
                ids = profile.get("linked_character_asset_ids") or attrs.get("linked_character_asset_ids") or []
                explicit = profile.get("character_asset_id") or profile.get("linked_character_asset_id") or attrs.get("linked_character_asset_id")
                for target in ([explicit] if explicit else ids):
                    append(target, "character", stable=True)
        for prop in relation["props"]:
            append(prop["asset_id"], "prop", presence=prop["presence"])
        context = {"version": VERSION, "shared_design": relation.get("design_description") or row["data"].get("consistency_design") or "",
                   "own_definition": _definition(row, relation, stable=True),
                   "relation": {key: relation[key] for key in (
            "identity_asset_id", "location_asset_id", "kind", "props", "design_description")},
            "dependencies": dependencies}
        output[ident] = {**context, "fingerprint": fingerprint(context)}
    return output


def retain_contract(prompt, context):
    if not context:
        return prompt
    relation = context["relation"]
    lines = []
    if context.get("shared_design"):
        lines.append("当前资产共同设计：" + context["shared_design"])
    for item in context["dependencies"]:
        source = item["design_description"] or item["accepted_prompt"] or item["description"]
        facts = "；".join(f"{key}={value}" for key, value in item["fields"].items())
        role = {"identity": "基础设计", "location": "所属地点空间", "character": "角色身份", "prop": "关联道具"}[item["role"]]
        if item.get("presence") == "occurrence":
            lines.append(f"临时出镜道具 {item['name']}：仅在对应片段按剧情使用，不固化到场景基准图。")
        else:
            lines.append(f"{role} {item['name']}：{source}\n已确定资料：{facts}")
    if relation["kind"] != "base":
        lines.append("只改变当前资料明确要求的状态或机位；保持基础设计的建筑、物件造型、材质和空间关系。")
    if not lines:
        return prompt
    return prompt + "\n\n共同设计约束（原文事实优先；未确定的细节不得另造冲突设计）：\n" + "\n".join(lines)
