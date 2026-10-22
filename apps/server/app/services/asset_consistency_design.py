"""Establish missing shared designs within the already requested text call."""

import json

from sqlalchemy import select, update

from app.models import ProjectAssetLink
from app.services.asset_consistency_inference import STATE_FIELDS, infer
from app.services.asset_consistency_prompt import _definition
from app.services.asset_consistency_service import project_rows


def requests(snapshots):
    selected = {row["id"] for row in snapshots}
    result = {}
    for snapshot in snapshots:
        context = snapshot.get("consistency_context") or {}
        own = context.get("own_definition")
        if (own and context["relation"]["kind"] == "base" and not context.get("shared_design")
                and (snapshot["type"] == "scene" or any(
                    (snapshot.get("profile") or {}).get(field) or (snapshot.get("attributes") or {}).get(field)
                    for field in STATE_FIELDS))):
            result[snapshot["id"]] = {**own, "role": "identity", "presence": None}
        for dependency in context.get("dependencies", []):
            if (dependency["asset_id"] not in selected and not dependency["design_description"]
                    and dependency.get("presence") != "occurrence"):
                result[dependency["asset_id"]] = dependency
    return list(result.values())


async def establish(session, job, raw, *, allowed_ids):
    allowed = {row["asset_id"] for row in job.payload.get("shared_design_requests", [])} & allowed_ids
    if not allowed:
        return
    try:
        output = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    except (TypeError, ValueError):
        return
    designs = output.get("designs") if isinstance(output, dict) else None
    if not isinstance(designs, list):
        return
    rows = await project_rows(session, job.project_id)
    by_id, relations = {row["asset_id"]: row for row in rows}, infer(rows)
    expected = {row["asset_id"]: row for row in job.payload.get("shared_design_requests", [])}
    seen = set()
    for design in designs:
        if not isinstance(design, dict):
            continue
        ident, text = design.get("asset_id"), design.get("design_description")
        if (type(ident) is not int or ident not in allowed or ident in seen
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000):
            continue
        seen.add(ident)
        if ident not in by_id:
            continue
        original = expected[ident]
        current = _definition(by_id[ident], relations[ident], stable=original["role"] in {"identity", "location", "character"})
        if any(current[key] != original[key] for key in ("fields", "description", "accepted_prompt")):
            continue
        link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == job.project_id, ProjectAssetLink.asset_id == ident,
            ProjectAssetLink.production_archived.is_(False)))
        if not link:
            continue
        await session.refresh(link)
        data = link.production_data or {}
        if data.get("consistency_design") or (data.get("consistency") or {}).get("design_description"):
            continue
        await session.execute(update(ProjectAssetLink).where(
            ProjectAssetLink.id == link.id, ProjectAssetLink.production_revision == link.production_revision,
            ProjectAssetLink.production_archived.is_(False),
        ).values(production_data={**data, "consistency_design": text.strip(), "consistency_design_job_id": job.id},
                 production_revision=link.production_revision + 1).execution_options(synchronize_session=False))
        await session.refresh(link)
