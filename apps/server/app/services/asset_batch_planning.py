"""Read-only scoped image dependency plans and confirmation fingerprints."""

# ruff: noqa: RUF001

import hashlib
import hmac
import json
import re

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import Asset, Job, ProjectAssetLink, Provider, ProviderModel, StylePreset
from app.providers.protocols import validate_model_protocol
from app.services import asset_service
from app.services.asset_character_representation import character_profile, is_group
from app.services.asset_consistency_images import (
    image_context,
    image_dependencies,
    prerequisite_binding,
)
from app.services.costume_generation_service import resolve_mode, views
from app.services.image_model_contract import reference_limit, validate_image_inputs
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.job_pricing_service import _aggregate_video_pricing
from app.services.pricing_service import estimate
from app.services.style_generation_service import project_style_media

CONTRACT = "asset-image-batch.v6"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def token(value):
    return hmac.new(settings.jwt_secret.encode(), digest(value).encode(), hashlib.sha256).hexdigest()


def source_fingerprint(asset, link):
    data = link.production_data or {}
    return digest({"description": asset.description, "prompt": asset.prompt_anchor,
                   "attributes": asset.attributes,
                   "data": {key: value for key, value in data.items() if key not in {
                       "adoptions", "consistency_image_basis", "consistency_design_job_id"}}})


def effective_anchor(asset, link, *, dependency_only=False):
    """Implicit prerequisites may use an accepted stable design instead of a prompt."""
    data = link.production_data or {}
    prompt = data.get("prompt_anchor", asset.prompt_anchor)
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    if dependency_only:
        for design in ((data.get("consistency") or {}).get("design_description"),
                       data.get("consistency_design")):
            if isinstance(design, str) and design.strip():
                return design.strip()
    return ""


def expand_references(rows, prompt):
    """Use the catalog's slug boundary rules without rebuilding the whole catalog per node."""
    refs = []
    for asset, link in sorted(rows.values(), key=lambda pair: len(pair[1].local_slug), reverse=True):
        pattern = re.compile(rf"@{re.escape(link.local_slug)}(?![\w\u4e00-\u9fff-])")
        if not pattern.search(prompt):
            continue
        refs.append(asset.id)
        anchor = (link.production_data or {}).get("prompt_anchor", asset.prompt_anchor) or asset.description or asset.name
        replacement = f"{asset.name} ({anchor})"
        prompt = pattern.sub(lambda _match, replacement=replacement: replacement, prompt)
    return prompt, refs


async def preview(session, project, *, asset_ids, provider_model_id, parameters,
                  negative_prompt=None, generation_mode="missing", scope="selected",
                  prepare_prompts=False, text_model_id=None, **_ignored):
    from app.services.asset_batch_service import (
        _character_view_parameters,
        _effective_image_parameters,
    )

    if generation_mode not in {"missing", "regenerate"} or scope not in {"selected", "expanded"}:
        raise ConflictError("图片批次的范围或生成模式无效")
    selected = list(dict.fromkeys(asset_ids))
    parameters = _effective_image_parameters(project, parameters)
    model = await session.get(ProviderModel, provider_model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if not model or not provider or model.model_type != "image" or not model.enabled or not provider.enabled:
        raise ConflictError("图片模型或渠道不可用")
    validate_model_protocol(provider, model)
    parameters = {**(model.default_params or {}), **parameters}
    context = await image_context(session, project.id)
    pairs = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == project.id))).all()
    rows = {asset.id: (asset, link) for asset, link in pairs}
    if not selected or any(ident not in rows for ident in selected):
        raise ConflictError("所选资产不属于当前项目")
    active = set((await session.scalars(select(Job.target_id).where(
        Job.project_id == project.id, Job.target_type == "asset", Job.job_type == "image",
        Job.status.not_in(TERMINAL_STATUSES), Job.deleted_at.is_(None),
    ))).all())
    bindings, dependencies, anchors, expanded_prompts, explicit_refs, modes = {}, {}, {}, {}, {}, {}
    problems, visiting, ordered, seen = {}, set(), [], set()
    relation_fingerprints = {}

    async def binding(ident):
        if ident not in bindings:
            try:
                bindings[ident] = await prerequisite_binding(session, project.id, ident, context=context)
            except ConflictError:
                if ident in rows and (rows[ident][1].production_data or {}).get("consistency_image_basis"):
                    raise
                bindings[ident] = None
        return bindings[ident]

    async def visit(ident):
        if ident in visiting:
            raise ConflictError("图片依赖形成循环，请调整资产关系")
        if ident in seen:
            if ident in selected and generation_mode == "regenerate" and ident not in ordered:
                seen.remove(ident)
            else:
                return
        if ident not in rows:
            raise ConflictError("图片前置资产不属于当前项目")
        visiting.add(ident)
        asset, link = rows[ident]
        anchor = effective_anchor(asset, link, dependency_only=ident not in selected)
        anchors[ident] = anchor
        modes[ident] = await resolve_mode(session, project.id, asset, parameters)
        if asset.project_id != project.id:
            problems[ident] = ("shared_asset_requires_local_variant", "公共素材需先建立项目变体")
        elif link.production_archived:
            problems[ident] = ("archived", "资产已归档，请先恢复")
        elif asset.asset_type not in {"character", "scene", "prop", "costume", "reference"}:
            problems[ident] = ("unsupported_media_type", "该资产类型不使用图片模型")
        plan = {"dependencies": []}
        if await binding(ident) is not None and (ident not in selected or generation_mode == "missing"):
            dependencies[ident] = []
            expanded_prompts[ident], explicit_refs[ident] = anchor, []
            visiting.remove(ident)
            seen.add(ident)
            ordered.append(ident)
            return
        if ident not in problems and asset.asset_type != "reference":
            plan = await image_dependencies(session, project.id, ident, costume_mode=modes[ident] or "worn", context=context)
        expanded_prompt, refs = expand_references(rows, anchor)
        expanded_prompts[ident] = expanded_prompt
        explicit_refs[ident] = [ref for ref in refs if ref != ident]
        deps = list(dict.fromkeys([*(item["asset_id"] for item in plan["dependencies"]), *explicit_refs[ident]]))
        dependencies[ident] = deps
        relation_fingerprints[ident] = plan.get("relation_fingerprint")
        for dep in deps:
            if dep not in rows:
                raise ConflictError("图片前置资产不属于当前项目")
            if await binding(dep) is None or (dep in selected and generation_mode == "regenerate"):
                await visit(dep)
            else:
                seen.add(dep)
        visiting.remove(ident)
        seen.add(ident)
        if ident not in ordered:
            ordered.append(ident)

    for ident in selected:
        await visit(ident)
    expanded = list(dict.fromkeys([*selected, *ordered, *sorted(seen)]))
    generated, reused, skipped, blockers, items = [], [], [], [], []
    for ident in ordered:
        asset, link = rows[ident]
        adopted = await binding(ident)
        reused_here = adopted is not None and (ident not in selected or generation_mode == "missing")
        reason = None
        code = None
        if reused_here:
            reused.append(ident)
            status = "reused"
        elif ident not in selected and scope == "selected":
            status, code, reason = "blocked", "dependency_outside_scope", "缺少的前置资产不在所选范围，请明确选择扩展范围"
        elif ident in problems:
            status = "skipped" if ident in selected else "blocked"
            code, reason = problems[ident]
        elif ident in active:
            status, code, reason = "blocked", "active_job", "已有进行中的图片任务，请等待完成后重新预览"
        elif not anchors[ident] and (not prepare_prompts or asset.asset_type == "reference"):
            status, code, reason = "blocked", "missing_prompt", "缺少资产一致性提示词，请先生成或编辑文字提示词"
        else:
            generated.append(ident)
            status = "waiting" if dependencies[ident] else "ready"
        item = {"asset_id": ident, "asset_name": asset.name, "name": asset.name,
                "status": status, "prerequisite_asset_ids": dependencies[ident], "reason": reason}
        items.append(item)
        if code:
            entry = {"asset_id": ident, "asset_name": asset.name, "code": code, "reason": reason}
            (skipped if status == "skipped" else blockers).append(entry)
    # Adopted prerequisites are leaves: their old prompts/dependencies are irrelevant.
    for ident in seen - set(ordered):
        reused.append(ident)
        items.append({"asset_id": ident, "asset_name": rows[ident][0].name, "name": rows[ident][0].name,
                      "status": "reused", "prerequisite_asset_ids": [], "reason": None})
    prompt_ids = [ident for ident in generated if not anchors[ident]]
    text_model, text_execution = None, None
    if prompt_ids:
        from app.services.business_executor_service import resolve_execution
        executor = await resolve_execution(session, "asset_prompt_generator", requested_model_id=text_model_id)
        text_model = executor.get("model")
        text_execution = executor
        if not text_model:
            raise ConflictError("请先在模型设置中配置提示词文本模型")
    style_id = await project_style_media(session, project.id, project.owner_id, "image")
    style_key = str((project.creation_settings or {}).get("style_id", ""))
    style = await session.get(StylePreset, int(style_key[7:])) if style_key.startswith("preset:") and style_key[7:].isdigit() else None
    quotes, count = [], 0
    for ident in generated:
        asset, link = rows[ident]
        refs, pending = set(), set()
        for dep in dependencies[ident]:
            adopted = await binding(dep)
            if adopted:
                refs.add(adopted["media_file_id"])
            else:
                pending.add(dep)
        own = await binding(ident)
        if own:
            refs.add(own["media_file_id"])
        if style_id:
            refs.add(style_id)
        group = is_group(character_profile(asset.attributes, link.production_data))
        view_defs = asset_service.character_reference_sheet_views() if asset.asset_type == "character" and not group else [None] * (len(views(parameters.get("costume_views"))) if asset.asset_type == "costume" else 1)
        for index, view in enumerate(view_defs):
            params = _character_view_parameters(model, parameters, view) if view else parameters
            validate_image_inputs(model, params, list(refs))
            # Additional directions consume the primary image's own reference slot.
            required_slots = len(refs) + len(pending) + int(index > 0)
            if required_slots > reference_limit(model):
                blockers.append({"asset_id": ident, "asset_name": asset.name,
                                 "code": "reference_limit", "reason": f"需要 {required_slots} 张参考图，当前模型上限为 {reference_limit(model)} 张"})
            quotes.append(estimate(model, expanded_prompts[ident], params))
            count += 1
    pricing = _aggregate_video_pricing(quotes)
    pricing["reason"] = "按资产图片范围估算；价格未知不等于免费，实际费用以渠道账单为准"
    result = {"required_contract": CONTRACT, "scope": scope, "selected_asset_ids": selected,
              "prompt_asset_ids": prompt_ids, "text_model_id": text_model["id"] if text_model else None,
              "text_model_name": text_model.get("name") if text_model else None,
              "expanded_asset_ids": expanded, "dependency_asset_ids": [ident for ident in expanded if ident not in selected],
              "reused_asset_ids": list(dict.fromkeys(reused)), "asset_ids": generated,
              "dependencies": [{"asset_id": ident, "prerequisite_asset_ids": dependencies.get(ident, []),
                                "explicit_asset_ids": explicit_refs.get(ident, []),
                                "relation_fingerprint": relation_fingerprints.get(ident),
                                "source_fingerprint": source_fingerprint(*rows[ident]),
                                "own_binding": bindings.get(ident),
                                "frozen_bindings": {str(dep): bindings[dep] for dep in [ident, *dependencies.get(ident, [])] if bindings.get(dep)}} for ident in generated],
              "items": items, "skipped": skipped, "blockers": blockers,
              "planned_view_count": count, "estimated_cost": pricing, "can_create": bool(generated) and not blockers}
    result["expanded_prompts"] = {str(ident): expanded_prompts[ident] for ident in generated}
    source = {str(ident): {"description": rows[ident][0].description, "attributes": rows[ident][0].attributes,
                          "prompt": rows[ident][0].prompt_anchor, "revision": rows[ident][1].production_revision,
                          "data": rows[ident][1].production_data, "binding": bindings.get(ident),
                          "expanded_prompt": expanded_prompts.get(ident)} for ident in expanded}
    result["preview_token"] = token({"project_id": project.id, "owner_id": project.owner_id,
        "request": {"asset_ids": selected, "provider_model_id": provider_model_id, "parameters": parameters,
                    "negative_prompt": negative_prompt, "generation_mode": generation_mode, "scope": scope,
                    "prepare_prompts": prepare_prompts, "text_model_id": text_model_id},
        "source": source, "plan": result, "settings": project.creation_settings,
        "text_execution": text_execution,
        "style": {"id": style.id, "suffix": style.prompt_suffix, "negative": style.negative_prompt,
                  "media": style_id, "enabled": style.enabled} if style else None,
        "model": {"model_id": model.model_id, "default_params": model.default_params, "pricing": model.pricing,
                  "capabilities": model.capabilities, "protocol": model.api_protocol, "provider_id": provider.id,
                  "base_url": provider.base_url, "provider_protocol": provider.protocol}})
    return result
