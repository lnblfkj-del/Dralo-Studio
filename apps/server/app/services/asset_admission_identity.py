"""Bind formal representations after all candidate IDs have formal asset IDs."""

from copy import deepcopy

from sqlalchemy import select

from app.models import Asset, ProjectAssetLink

PROJECTION_KEYS = ("character_identity", "character_alias_claims", "field_trust", "production_fields",
                   "generation_description", "generation_description_source", "generation_prompt_source", "facts")


async def bind_admitted_identities(session, project_id, candidates):
    rows = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == project_id, ProjectAssetLink.production_archived.is_(False)))).all()
    by_id = {asset.id: (asset, link) for asset, link in rows}
    formal_ids = {row["candidate_id"]: row.get("formal_asset_id") or row.get("matched_asset_id")
                  for row in candidates if row.get("selected", True) and not row.get("merged_into_candidate_id")}
    for candidate in candidates:
        if candidate.get("candidate_id") not in formal_ids:
            continue
        pair = by_id.get(formal_ids[candidate["candidate_id"]])
        if pair is None:
            continue
        asset, link = pair
        attrs = candidate.get("attributes") or {}
        projection = {key: deepcopy(attrs[key]) for key in PROJECTION_KEYS if key in attrs}
        projection["aliases"] = list(candidate.get("aliases") or [])
        identity = projection.get("character_identity")
        if identity:
            base = formal_ids.get(identity.get("base_candidate_id"))
            if base in by_id and by_id[base][0].asset_type == "character" and base != asset.id:
                identity["base_asset_id"] = base
        data = dict(link.production_data or {})
        data["extraction_projection"] = projection
        if data != link.production_data:
            link.production_data = data
            link.production_revision += 1

    # Never let a later same-name group overwrite the earlier person's identity.
    names = {}
    for asset, link in rows:
        if asset.asset_type != "character":
            continue
        attrs = {**(asset.attributes or {}), **((link.production_data or {}).get("extraction_projection") or {})}
        for name in [asset.name, *(attrs.get("aliases") or [])]:
            names.setdefault(str(name).strip().casefold(), set()).add(asset.id)
    for asset, link in rows:
        if asset.id not in set(formal_ids.values()) or asset.asset_type not in {"voice", "costume"}:
            continue
        attrs = asset.attributes or {}
        from app.services.asset_extraction_voice import is_standalone_extraction_voice
        if asset.asset_type == "voice" and is_standalone_extraction_voice(attrs):
            continue
        import re
        labels = [label.strip().casefold() for label in re.split(r"[,\uFF0C\u3001/]+", str(attrs.get("character_name") or ""))
                  if label.strip()]
        linked = [next(iter(names[label])) for label in labels if len(names.get(label, set())) == 1]
        complete = bool(labels) and len(linked) == len(labels)
        relation = {"linked_character_asset_ids": list(dict.fromkeys(linked)) if complete else [],
                    "linked_character_asset_id": linked[0] if complete and len(linked) == 1 else None,
                    "character_relation_status": "linked" if complete else "needs_review"}
        data = deepcopy(link.production_data or {})
        profile = data.setdefault("profile", {})
        profile.update(relation)
        if asset.asset_type == "voice":
            profile["character_asset_id"] = relation["linked_character_asset_id"]
        if data != link.production_data:
            link.production_data = data
            link.production_revision += 1
