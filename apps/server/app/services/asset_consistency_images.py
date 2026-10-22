"""Project-local image dependencies and immutable generation reference contracts."""

# ruff: noqa: RUF001

from copy import deepcopy

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import AssetVersion, MediaFile, Project, ProjectAssetLink
from app.services.asset_binding_service import resolve_asset_binding
from app.services.asset_consistency_inference import infer
from app.services.asset_consistency_service import fingerprint, lock_project, project_rows
from app.services.image_model_contract import validate_image_inputs

BASIS_KEY = "consistency_image_basis"
SNAPSHOT_KEY = "consistency_image_snapshot"


async def image_context(session, project_id):
    """Request-scoped graph; discard after mutations that change definitions."""
    rows = await project_rows(session, project_id)
    return {"project_id": project_id, "rows": rows, "by_id": {row["asset_id"]: row for row in rows},
            "relations": infer(rows)}


def _definition_fingerprint(context, asset_id):
    from app.services.asset_consistency_prompt import _definition
    row, relation = context["by_id"][asset_id], context["relations"][asset_id]
    definition = _definition(row, relation)
    definition.pop("design_job_id", None)
    return fingerprint({"definition": definition, "relation": {
        key: relation[key] for key in ("identity_asset_id", "location_asset_id", "kind", "props", "design_description")}})


async def image_dependencies(session, project_id, asset_id, *, costume_mode="worn", context=None):
    """Planner API: required assets, ordered by semantic role, without media lookup."""
    context = context if context is not None else await image_context(session, project_id)
    by_id = context["by_id"]
    if asset_id not in by_id:
        raise ConflictError("图片资产不属于当前项目或已归档")
    relations = context["relations"]

    def root(ident):
        visited = set()
        while relations[ident].get("identity_asset_id"):
            if ident in visited:
                raise ConflictError("资产身份关联存在循环，请先修正关联")
            visited.add(ident)
            ident = relations[ident]["identity_asset_id"]
            if ident not in by_id:
                raise ConflictError("关联的身份资产不属于当前项目")
        return ident

    family = root(asset_id)
    deps, seen, expanded = [], set(), set()

    def add(ident, role):
        original = ident
        ident = root(ident)
        key = (ident, role)
        if ident != asset_id and key not in seen:
            seen.add(key)
            deps.append({"asset_id": ident, "role": role})
        expand(ident)
        expand(original)

    def expand(ident):
        if ident in expanded:
            return
        expanded.add(ident)
        relation = relations[ident]
        if relation.get("identity_asset_id"):
            expand(relation["identity_asset_id"])
        if relation.get("location_asset_id"):
            add(relation["location_asset_id"], "location")
        for prop in relation.get("props", []):
            if prop["presence"] == "fixed":
                add(prop["asset_id"], "fixed_prop")

    if family != asset_id:
        add(family, "identity")
    expand(asset_id)
    row = by_id[asset_id]
    if row["asset_type"] == "costume" and costume_mode != "garment_only":
        attrs, profile = row["attributes"], row["data"].get("profile") or {}
        ids = profile.get("linked_character_asset_ids") or attrs.get("linked_character_asset_ids") or []
        explicit = profile.get("character_asset_id") or profile.get("linked_character_asset_id")
        if explicit:
            ids = [explicit]
        elif not ids:
            ids = [attrs.get("linked_character_asset_id")]
        ids = list(dict.fromkeys(value for value in ids if type(value) is int and value > 0))
        if not ids:
            raise ConflictError("服装尚未关联角色，请先关联角色并采用角色图片")
        for ident in ids:
            if ident not in by_id or by_id[ident]["asset_type"] != "character":
                raise ConflictError("服装关联的角色不存在或不属于当前项目")
            # Preserve the selected character's own adoption, including group assets.
            if (ident, "character") not in seen:
                seen.add((ident, "character"))
                deps.append({"asset_id": ident, "role": "character"})
    return {"family_root_asset_id": family, "dependencies": deps,
            "relation_fingerprint": fingerprint({str(ident): relations[ident] for ident in
                                                 {asset_id, family, *expanded, *(item["asset_id"] for item in deps)}})}


async def _link(session, project_id, asset_id):
    return await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == asset_id,
        ProjectAssetLink.production_archived.is_(False)).execution_options(populate_existing=True))


async def _binding(session, project, asset_id, *, basis=False, optional=False, persist=False, context=None):
    context = context if context is not None else await image_context(session, project.id)
    row = context["by_id"].get(asset_id)
    link = row["link"] if row is not None else None
    if link is None:
        raise ConflictError("参考资产不存在或已归档")
    # New work follows the visible adopted image. Running jobs keep their own
    # exact reference snapshot, independent of later adoption changes.
    definition_fingerprint = _definition_fingerprint(context, asset_id)
    try:
        choices = (link.production_data or {}).get("adoptions") or []
        preferred = next((key for key in ("default", "environment:default", "appearance:default", "appearance:base")
                          if any(item.get("key") == key for item in choices)), None)
        binding = await resolve_asset_binding(session, project, asset_id=asset_id, expected_kind="image",
                                              adoption_key=preferred)
    except ConflictError as exc:
        if optional and any(label in exc.message for label in ("尚未采用素材", "尚未选择可用版本")):
            return None
        raise
    if binding["view_type"] not in {"base", "appearance"}:
        if optional:
            return None
        raise ConflictError("参考资产需要先采用基础图或外观主图")
    media = await session.get(MediaFile, binding["media_file_id"])
    binding = {**binding, "binding_source": "adoption", "media_hash": media.hash,
               "media_path": media.file_path}
    if persist:
        link.production_data = {**(link.production_data or {}), BASIS_KEY: {
            "version": 1, "binding": deepcopy(binding), "definition_fingerprint": definition_fingerprint}}
        await session.flush()
    return binding


async def prerequisite_binding(session, project_id, asset_id, *, context=None):
    """Scheduler readiness uses the same frozen-basis/adoption policy as submission."""
    return await _binding(session, await session.get(Project, project_id), asset_id, basis=True, context=context)


async def freeze_image_references(session, project_id, asset_id, model, prompt, references, *,
                                  parameters=None, costume_mode="worn", persist=True, freeze=None, context=None,
                                  binding_overrides=None):
    """Submission/readiness API: freeze exact bindings; never truncate references."""
    if freeze is not None:
        persist = freeze
    if not project_id:
        return prompt, list(references), None
    if persist:
        await lock_project(session, project_id)
    context = context if context is not None else await image_context(session, project_id)
    plan = await image_dependencies(session, project_id, asset_id, costume_mode=costume_mode, context=context)
    project = await session.get(Project, project_id)
    async def chosen(ident, optional=False):
        if binding_overrides is not None and str(ident) in binding_overrides:
            return deepcopy(binding_overrides[str(ident)])
        return await _binding(session, project, ident, basis=True, optional=optional, context=context)
    bindings = []
    for item in plan["dependencies"]:
        binding = await chosen(item["asset_id"])
        bindings.append({**binding, "role": item["role"]})
    # Extra angles and regeneration use the currently adopted primary image.
    row = context["by_id"][asset_id]
    own = await chosen(asset_id, optional=True)
    if own:
        bindings.insert(0, {**own, "role": "same_scene" if row["asset_type"] == "scene" else "same_asset"})
    refs = list(dict.fromkeys([*(item["media_file_id"] for item in bindings), *references]))
    validate_image_inputs(model, parameters or {}, refs)
    if persist:
        for item in bindings:
            await _binding(session, project, item["asset_id"], basis=True, persist=True, context=context)
    labels = {"identity": "同族资产身份基准", "location": "所属地点空间基准",
              "fixed_prop": "场景固定道具", "character": "服装关联角色身份",
              "same_scene": "当前采用的场景空间参考", "same_asset": "当前采用的本资产外观参考"}
    lines = []
    for item in bindings:
        item["reference_index"] = refs.index(item["media_file_id"]) + 1
        item["purpose"] = item["role"]
        lines.append(f"参考图{item['reference_index']}：{labels[item['role']]}"
                     f"（{item.get('asset_name') or item['asset_id']}）。")
    if lines:
        prompt += "\n\n已冻结的图片一致性参考：\n" + "\n".join(lines)
        prompt += "\n保持身份、物种、几何结构、材质与空间布局一致，仅改变本次要求的状态或视角。"
    for media_id in references:
        if media_id not in {item["media_file_id"] for item in bindings}:
            media = await session.get(MediaFile, media_id)
            if media is None or media.kind != "image":
                raise ConflictError("指定的参考图片不存在或不可用")
            bindings.append({"media_file_id": media_id, "role": "explicit_reference", "purpose": "explicit_reference",
                             "reference_index": refs.index(media_id) + 1,
                             "media_hash": media.hash, "media_path": media.file_path})
    snapshot = {"version": 1, **plan, "references": bindings,
                "definition_fingerprint": _definition_fingerprint(context, asset_id)}
    snapshot["fingerprint"] = fingerprint(snapshot)
    return prompt, refs, snapshot


async def establish_image_basis(session, job, version, media):
    """First successfully persisted primary image wins, independently of adoption."""
    snapshot = (job.payload or {}).get(SNAPSHOT_KEY)
    if (not job.project_id or not snapshot
            or media is None or media.kind != "image" or version.asset_id != job.target_id
            or version.media_file_id != media.id or version.review_status == "archived"
            or version.view_type not in {"base", "appearance"}
            or "qa:landscape-orientation-mismatch" in (version.tags or [])):
        return
    await lock_project(session, job.project_id)
    link = await _link(session, job.project_id, job.target_id)
    if link is None or (link.production_data or {}).get(BASIS_KEY):
        return
    context = await image_context(session, job.project_id)
    current = await image_dependencies(session, job.project_id, job.target_id, context=context,
                                       costume_mode=(job.payload.get("parameters") or {}).get("costume_mode", "worn"))
    definition_fingerprint = _definition_fingerprint(context, job.target_id)
    if (current["relation_fingerprint"] != snapshot["relation_fingerprint"]
            or snapshot.get("definition_fingerprint") != definition_fingerprint):
        return
    binding = {"asset_id": job.target_id,
               "asset_version_id": version.id, "media_file_id": media.id,
               "media_kind": "image", "view_type": version.view_type,
               "media_hash": media.hash, "media_path": media.file_path, "source_job_id": job.id}
    link.production_data = {**(link.production_data or {}), BASIS_KEY: {
        "version": 1, "binding": binding, "input_fingerprint": snapshot["fingerprint"],
        "definition_fingerprint": definition_fingerprint}}
    await session.flush()


async def validate_frozen_image_references(session, job):
    """Execution guard: stored bindings cannot be retargeted while a job waits."""
    payload = job.payload or {}
    refs = payload.get("reference_media_ids") or []
    bindings = [*((payload.get(SNAPSHOT_KEY) or {}).get("references") or []),
                *(payload.get("image_reference_roles") or [])]
    style = payload.get("style_snapshot") or {}
    if style.get("media_id"):
        bindings.append({**style, "media_file_id": style["media_id"]})
    for binding in bindings:
        media_id = binding["media_file_id"]
        media = await session.get(MediaFile, media_id)
        if media_id not in refs or media is None or media.kind != "image":
            raise ConflictError("冻结的参考图片已失效或被移除")
        if (("media_hash" in binding and media.hash != binding["media_hash"])
                or ("media_path" in binding and media.file_path != binding["media_path"])):
            raise ConflictError("冻结的参考图片内容已改变，请重新预览并确认")
        if binding.get("reference_index") not in (None, refs.index(media_id) + 1):
            raise ConflictError("冻结的参考图片顺序已改变，请重新预览并确认")
        if binding.get("asset_version_id"):
            version = await session.get(AssetVersion, binding["asset_version_id"])
            if (version is None or version.asset_id != binding["asset_id"] or version.media_file_id != media_id
                    or version.review_status == "archived"):
                raise ConflictError("冻结的参考图片版本已改变，请重新预览并确认")
