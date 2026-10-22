"""Read and edit project-local visual identity contracts."""

# ruff: noqa: RUF001

import hashlib
import json

from sqlalchemy import select, update

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Asset, AssetVersion, Episode, Project, ProjectAssetLink
from app.services.asset_consistency_inference import VERSION, VISUAL_TYPES, infer, scope_key
from app.services.asset_segment_projection import effective_attributes


async def project_rows(session, project_id):
    pairs = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == project_id, ProjectAssetLink.production_archived.is_(False))
      .execution_options(populate_existing=True))).all()
    episodes = {episode.number: episode for episode in (await session.scalars(
        select(Episode).where(Episode.project_id == project_id))).all()}
    from app.services.asset_evidence_relation import scene_at
    scene_cache = {}
    def valid_usage(usage):
        episode = episodes.get(usage.get("episode_number"))
        if (episode is None or usage.get("script_revision") != episode.script_revision
                or usage.get("alignment_status") != "exact" or usage.get("needs_review")):
            return None
        script, quote = episode.script or "", str(usage.get("source_excerpt") or "")
        start, end = usage.get("start"), usage.get("end")
        if not quote.strip():
            return None
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(script)
                and script[start:end] == quote):
            if script.count(quote) != 1:
                return None
            start, end = script.index(quote), script.index(quote) + len(quote)
        headers = []
        for offset in (start, end - 1):
            key = (episode.id, offset)
            if key not in scene_cache:
                scene_cache[key] = scene_at({"text": script}, offset)
            headers.append(scene_cache[key])
        if headers[0][0] < 0 or headers[0] != headers[1]:
            return None
        return {**usage, "consistency_scene_key": f"ep:{episode.id}:r{episode.script_revision}:scene:{headers[0][0]}"}
    rows = []
    for asset, link in pairs:
        if asset.asset_type not in VISUAL_TYPES:
            continue
        data = dict(link.production_data or {})
        attrs = effective_attributes(asset.attributes, data)
        scope = dict(data.get("script_scope") or {})
        usages = scope.get("usage_records", attrs.get("usage_records", []))
        scope["usage_records"] = [verified for usage in usages if (verified := valid_usage(usage)) is not None]
        data["script_scope"] = {**attrs, **scope} if not data.get("script_scope") else scope
        rows.append({"asset_id": asset.id, "name": asset.name, "asset_type": asset.asset_type,
                     "revision": link.production_revision, "description": asset.description or "",
                     "prompt": data.get("prompt_anchor", asset.prompt_anchor) or "",
                     "attributes": attrs, "data": data, "asset": asset, "link": link})
    return rows


async def graph(session, project_id):
    rows = await project_rows(session, project_id)
    relations = infer(rows)
    versions = (await session.scalars(select(AssetVersion).where(
        AssetVersion.asset_id.in_([row["asset_id"] for row in rows]),
        AssetVersion.review_status != "archived",
        AssetVersion.view_type.in_(["base", "appearance"]),
    ))).all()
    by_version = {version.id: version for version in versions}
    def media_id(row):
        choices = row["data"].get("adoptions")
        if choices is not None:
            choice = next((item for key in ("default", "environment:default", "appearance:default", "appearance:base")
                           for item in choices if item.get("key") == key), None)
            if choice is None and len(choices) == 1:
                choice = choices[0]
            version = by_version.get((choice or {}).get("version_id"))
        else:
            version = next((item for item in versions if item.asset_id == row["asset_id"] and item.is_final), None)
        return version.media_file_id if version and version.asset_id == row["asset_id"] else None
    return {"version": VERSION, "items": [
        {key: row[key] for key in ("asset_id", "name", "asset_type", "revision")} | relations[row["asset_id"]]
        | {"design_description": relations[row["asset_id"]].get("design_description") or row["data"].get("consistency_design") or "",
           "reference_media_id": media_id(row)}
        for row in rows
    ]}


def validate_relations(rows, relations):
    by_id = {row["asset_id"]: row for row in rows}
    for ident, relation in relations.items():
        row = by_id[ident]
        for field in ("identity_asset_id", "location_asset_id"):
            target = relation.get(field)
            if target is None:
                continue
            other = by_id.get(target)
            if other is None or target == ident:
                raise ValidationError("关联资产必须属于当前项目且不能是自身")
            if other["asset_type"] != row["asset_type"] or (field == "location_asset_id" and row["asset_type"] != "scene"):
                raise ValidationError("基础资产须为同一分类，所属地点须为场景")
            if scope_key(row) != scope_key(other):
                raise ValidationError("不同故事单元的资产不能直接共用设计，请先核对所属故事")
        if relation["props"] and row["asset_type"] != "scene":
            raise ValidationError("只有场景能设置场景道具")
        for prop in relation["props"]:
            other = by_id.get(prop["asset_id"])
            if other is None or other["asset_type"] != "prop" or scope_key(row) != scope_key(other):
                raise ValidationError("关联道具必须属于当前项目的相同故事范围")
    # Both edges carry definitions; mixed location/identity cycles are invalid too.
    visited, visiting = set(), set()
    def visit(ident):
        if ident in visiting:
            raise ValidationError("资产关联形成循环，请调整基础资产或所属地点")
        if ident in visited:
            return
        visiting.add(ident)
        for field in ("identity_asset_id", "location_asset_id"):
            target = relations[ident].get(field)
            if target is not None:
                visit(target)
        visiting.remove(ident)
        visited.add(ident)
    for ident in relations:
        visit(ident)


async def lock_project(session, project_id):
    # Lock one common row before validating the graph, including on SQLite
    # where SELECT FOR UPDATE does not serialize independent asset edits.
    await session.execute(update(Project).where(Project.id == project_id).values(
        name=Project.name, updated_at=Project.updated_at).execution_options(synchronize_session=False))


async def patch_relation(session, project_id, asset_id, patch):
    await lock_project(session, project_id)
    rows = await project_rows(session, project_id)
    row = next((row for row in rows if row["asset_id"] == asset_id), None)
    if row is None:
        raise NotFoundError("资产不存在或已归档")
    relation = patch.model_dump(exclude={"expected_revision"})
    relations = infer(rows)
    relations[asset_id] = relation
    validate_relations(rows, relations)
    data = {**row["link"].production_data, "consistency": {"version": VERSION, **relation}}
    data.pop("consistency_design", None)
    data.pop("consistency_design_job_id", None)
    result = await session.execute(update(ProjectAssetLink).where(
        ProjectAssetLink.id == row["link"].id,
        ProjectAssetLink.production_revision == patch.expected_revision,
        ProjectAssetLink.production_archived.is_(False),
    ).values(production_data=data, production_revision=patch.expected_revision + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError("资产资料已变化，请刷新后保存关联")
    await session.refresh(row["link"])
    return await graph(session, project_id)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
