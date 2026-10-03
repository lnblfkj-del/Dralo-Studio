"""N7 deterministic backfill for narrative specs and script asset scopes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, CreationSession, Episode, Job, Project, ProjectAssetLink
from app.services.creation_asset_scope import (
    build_narrative_asset_scope,
    candidate_narrative_scope,
)
from app.services.narrative_spec_service import normalize_narrative_spec


ACTIVE_JOB_STATUSES = {"queued", "running", "processing", "retrying", "downloading"}
SCRIPT_SCOPE_KEYS = {
    "episode_numbers",
    "usage_records",
    "source_records",
    "source_script_revisions",
    "source_assignment_status",
    "script_dependency_status",
    "script_stale_reason",
    "source_kind",
    "requirement_type",
    "input_fingerprint",
}


def _migrated_settings(
    settings: dict[str, Any] | None,
    *,
    inherited_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    migrated = dict(settings or {})
    current = migrated.get("narrative_spec")
    if current is None and inherited_spec is not None:
        migrated["narrative_spec"] = dict(inherited_spec)
        return migrated
    migrated["narrative_spec"] = normalize_narrative_spec(
        current,
        episode_count=migrated.get("episode_count", 10),
        episode_duration=migrated.get("episode_duration", 90),
    )
    return migrated


def _episode_sources(episodes: list[Episode]) -> list[dict[str, Any]]:
    return [
        {
            "episode_id": episode.id,
            "episode_number": episode.number,
            "script_revision": episode.script_revision,
        }
        for episode in episodes
    ]


def _legacy_script_scope(
    attributes: dict[str, Any],
    narrative_scope: dict[str, Any],
    *,
    asset_type: str,
) -> dict[str, Any]:
    numbers = sorted({int(value) for value in attributes.get("episode_numbers") or []})
    preserved = {
        key: attributes[key]
        for key in SCRIPT_SCOPE_KEYS
        if key in attributes
    }
    scope = {
        **preserved,
        **candidate_narrative_scope(numbers, narrative_scope),
        "episode_numbers": numbers,
    }
    scope.setdefault("source_kind", "script_asset_breakdown")
    scope.setdefault("requirement_type", attributes.get("requirement_type") or asset_type)
    scope.setdefault("source_assignment_status", "episode" if numbers else "unassigned")
    scope.setdefault("script_dependency_status", "current")
    scope.setdefault("script_stale_reason", None)
    return scope


def _enrich_breakdown(
    settings: dict[str, Any],
    narrative_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    if not narrative_scope:
        return settings
    breakdown = settings.get("asset_breakdown")
    if not isinstance(breakdown, dict):
        return settings
    updated = dict(settings)
    migrated = dict(breakdown)
    candidates = []
    for value in migrated.get("candidates") or []:
        candidate = dict(value)
        candidate["narrative_scope"] = candidate_narrative_scope(
            [int(number) for number in candidate.get("episode_numbers") or []],
            narrative_scope,
        )
        candidates.append(candidate)
    migrated["candidates"] = candidates
    migrated["narrative_scope"] = narrative_scope
    migrated["narrative_spec_revision"] = narrative_scope.get(
        "narrative_spec_revision", 0
    )
    updated["asset_breakdown"] = migrated
    return updated


async def migrate_narrative_data(
    session: AsyncSession,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Backfill current contracts without inferring an unconfirmed story structure."""

    active_job_ids = list(
        (
            await session.scalars(
                select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.id)
            )
        ).all()
    )
    if apply and active_job_ids:
        raise RuntimeError(
            "Narrative migration requires an empty active job queue; "
            f"found jobs: {active_job_ids}"
        )

    projects = list((await session.scalars(select(Project).order_by(Project.id))).all())
    sessions = list(
        (await session.scalars(select(CreationSession).order_by(CreationSession.id))).all()
    )
    episodes = list((await session.scalars(select(Episode).order_by(Episode.id))).all())
    link_rows = (
        await session.execute(
            select(ProjectAssetLink, Asset)
            .join(Asset, Asset.id == ProjectAssetLink.asset_id)
            .order_by(ProjectAssetLink.id)
        )
    ).all()

    episodes_by_project: dict[int, list[Episode]] = {}
    for episode in episodes:
        episodes_by_project.setdefault(episode.project_id, []).append(episode)

    project_settings: dict[int, dict[str, Any]] = {}
    project_scopes: dict[int, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "schema_version": "n7.narrative_migration.v1",
        "apply": apply,
        "active_job_ids": active_job_ids,
        "projects_scanned": len(projects),
        "projects_updated": 0,
        "sessions_scanned": len(sessions),
        "sessions_updated": 0,
        "asset_links_scanned": len(link_rows),
        "asset_links_updated": 0,
        "local_assets_updated": 0,
        "shared_asset_links_unresolved": [],
    }

    for project in projects:
        migrated = _migrated_settings(project.creation_settings)
        project_settings[project.id] = migrated
        project_scopes[project.id] = build_narrative_asset_scope(
            migrated,
            _episode_sources(episodes_by_project.get(project.id, [])),
        )
        if migrated != dict(project.creation_settings or {}):
            report["projects_updated"] += 1
            if apply:
                project.creation_settings = migrated

    for item in sessions:
        inherited = (
            project_settings[item.project_id].get("narrative_spec")
            if item.project_id in project_settings
            else None
        )
        migrated = _migrated_settings(item.settings, inherited_spec=inherited)
        migrated = _enrich_breakdown(migrated, project_scopes.get(item.project_id))
        if migrated != dict(item.settings or {}):
            report["sessions_updated"] += 1
            if apply:
                item.settings = migrated

    for link, asset in link_rows:
        production_data = dict(link.production_data or {})
        attributes = dict(asset.attributes or {})
        existing_scope = production_data.get("script_scope")
        script_derived = attributes.get("source_kind") == "script_asset_breakdown"
        if not script_derived and not isinstance(existing_scope, dict):
            continue
        if not isinstance(existing_scope, dict) and asset.project_id != link.project_id:
            report["shared_asset_links_unresolved"].append(
                {"project_id": link.project_id, "asset_id": asset.id}
            )
            continue
        base = dict(existing_scope) if isinstance(existing_scope, dict) else attributes
        scope = _legacy_script_scope(
            base,
            project_scopes.get(link.project_id, {}),
            asset_type=asset.asset_type,
        )
        if production_data.get("script_scope") != scope:
            production_data["script_scope"] = scope
            report["asset_links_updated"] += 1
            if apply:
                link.production_data = production_data
                link.production_revision += 1
        if asset.project_id == link.project_id:
            migrated_attributes = {**attributes, **scope}
            if migrated_attributes != attributes:
                report["local_assets_updated"] += 1
                if apply:
                    asset.attributes = migrated_attributes

    if apply:
        await session.flush()
    report["shared_asset_links_unresolved"] = sorted(
        report["shared_asset_links_unresolved"],
        key=lambda item: (item["project_id"], item["asset_id"]),
    )
    report["total_updates"] = sum(
        int(report[key])
        for key in (
            "projects_updated",
            "sessions_updated",
            "asset_links_updated",
            "local_assets_updated",
        )
    )
    return report
