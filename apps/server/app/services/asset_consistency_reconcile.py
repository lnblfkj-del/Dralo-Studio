"""Recompile saved prompt bodies when a sibling establishes a shared design."""

from sqlalchemy import select, update

from app.models import Asset, Job, ProjectAssetLink
from app.services.asset_consistency_prompt import contexts, retain_contract
from app.services.asset_prompt_state import asset_snapshot, input_hash


def _sibling_design_only(before, after, sibling_ids):
    if not before or before["relation"] != after["relation"]:
        return False
    old = {item["asset_id"]: item for item in before["dependencies"]}
    new = {item["asset_id"]: item for item in after["dependencies"]}
    if old.keys() != new.keys():
        return False
    for ident in old:
        if old[ident] == new[ident]:
            continue
        ignored = {"design_description", "design_job_id"}
        if not new[ident].get("accepted_prompt") and new[ident].get("design_description"):
            ignored.add("accepted_prompt")
        if (new[ident].get("design_job_id") not in sibling_ids
                or {key: value for key, value in old[ident].items() if key not in ignored}
                != {key: value for key, value in new[ident].items() if key not in ignored}):
            return False
    return True


async def reconcile_batch(session, job):
    jobs = (await session.scalars(select(Job).where(Job.parent_job_id == job.parent_job_id,
                                                   Job.target_type == "asset_prompt_proposal"))).all()
    sibling_ids = {item.id for item in jobs}
    ids = {ident for item in jobs for ident in (item.payload or {}).get("asset_ids", [])}
    pairs = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == job.project_id, Asset.id.in_(ids),
            ProjectAssetLink.production_archived.is_(False)))).all()
    # Repeated passes allow a root/location chain to converge without an API
    # call or a second model charge. A depth bound prevents malformed old data.
    for _ in range(min(len(pairs), 8)):
        changed = False
        current = await contexts(session, job.project_id, ids)
        for asset, link in pairs:
            await session.refresh(link)
            marker = (link.production_data or {}).get("prompt_optimization") or {}
            context = current.get(asset.id)
            if (marker.get("job_id") not in sibling_ids or not marker.get("generated_body") or not context
                    or marker.get("consistency_fingerprint") == context["fingerprint"]
                    or marker.get("applied_hash") != input_hash(asset_snapshot(asset, link))
                    or not _sibling_design_only(marker.get("consistency_context"), context, sibling_ids)):
                continue
            prompt = retain_contract(marker["generated_body"], context)
            if len(prompt) > 4000:
                continue
            data = {**link.production_data, "prompt_anchor": prompt, "prompt_optimization": {
                **marker, "consistency_context": context, "consistency_fingerprint": context["fingerprint"],
                "applied_hash": input_hash({**asset_snapshot(asset, link), "current_prompt": prompt})}}
            written = await session.execute(update(ProjectAssetLink).where(
                ProjectAssetLink.id == link.id, ProjectAssetLink.production_revision == link.production_revision,
            ).values(production_data=data, production_revision=link.production_revision + 1).execution_options(synchronize_session=False))
            changed |= written.rowcount == 1
        if not changed:
            break
