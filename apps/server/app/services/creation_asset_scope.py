"""N6 narrative-aware episode scopes for script-derived assets."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Episode, ProjectAssetLink
from app.services.narrative_spec_service import narrative_spec_from_settings


def build_narrative_asset_scope(
    settings: dict[str, Any] | None,
    episode_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    spec = narrative_spec_from_settings(settings)
    units = list(spec.get("units") or [])
    episodes = []
    for source in episode_sources:
        number = int(source.get("episode_number") or 0)
        unit = next(
            (
                item
                for item in units
                if int(item.get("episode_start") or 0)
                <= number
                <= int(item.get("episode_end") or 0)
            ),
            None,
        )
        episodes.append(
            {
                "episode_id": source.get("episode_id"),
                "episode_number": number,
                "script_revision": source.get("script_revision"),
                "unit_id": unit.get("unit_id") if unit else None,
            }
        )
    return {
        "schema_version": "n6.asset_scope.v1",
        "narrative_structure": spec.get("structure"),
        "narrative_status": spec.get("status"),
        "narrative_spec_revision": spec.get("revision", 0),
        "character_reuse": spec.get("character_reuse"),
        "episodes": episodes,
    }


def candidate_narrative_scope(
    episode_numbers: list[int], narrative_scope: dict[str, Any] | None
) -> dict[str, Any]:
    scope = dict(narrative_scope or {})
    selected = {int(value) for value in episode_numbers}
    episode_rows = [
        dict(row)
        for row in scope.get("episodes") or []
        if int(row.get("episode_number") or 0) in selected
    ]
    return {
        "schema_version": scope.get("schema_version") or "n6.asset_scope.v1",
        "narrative_structure": scope.get("narrative_structure"),
        "narrative_status": scope.get("narrative_status"),
        "narrative_spec_revision": int(scope.get("narrative_spec_revision") or 0),
        "character_reuse": scope.get("character_reuse"),
        "unit_ids": sorted(
            {str(row["unit_id"]) for row in episode_rows if row.get("unit_id")}
        ),
        "episodes": episode_rows,
    }


def narrative_asset_scope_changed(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    """Compare only narrative fields that can change asset extraction scope."""

    def semantic_scope(value: dict[str, Any] | None) -> dict[str, Any]:
        scope = dict(value or {})
        episodes = sorted(
            (
                int(row.get("episode_number") or 0),
                str(row.get("unit_id") or ""),
            )
            for row in scope.get("episodes") or []
        )
        return {
            "narrative_structure": scope.get("narrative_structure"),
            "narrative_status": scope.get("narrative_status"),
            "character_reuse": scope.get("character_reuse"),
            "episodes": episodes,
        }

    return semantic_scope(before) != semantic_scope(after)


def refresh_script_scope(
    current: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    *,
    refreshed_episode_numbers: set[int],
    narrative_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    stored = dict(current or {})
    incoming = dict(candidate or {})
    old_numbers = {int(value) for value in stored.get("episode_numbers") or []}
    candidate_numbers = {int(value) for value in incoming.get("episode_numbers") or []}
    numbers = sorted(
        (old_numbers - refreshed_episode_numbers)
        | (candidate_numbers & refreshed_episode_numbers)
    )

    def refreshed_records(key: str) -> list[dict[str, Any]]:
        records = [
            dict(record)
            for record in stored.get(key) or []
            if int(record.get("episode_number") or 0) not in refreshed_episode_numbers
        ]
        records.extend(
            dict(record)
            for record in incoming.get(key) or []
            if int(record.get("episode_number") or 0) in refreshed_episode_numbers
        )
        return list(
            {
                json.dumps(record, ensure_ascii=False, sort_keys=True): record
                for record in records
            }.values()
        )

    scoped = candidate_narrative_scope(numbers, narrative_scope)
    source_revisions = {
        str(row["episode_id"]): int(row["script_revision"])
        for row in scoped["episodes"]
        if row.get("episode_id") is not None and row.get("script_revision") is not None
    }
    return {
        **scoped,
        "episode_numbers": numbers,
        "usage_records": refreshed_records("usage_records"),
        "source_records": refreshed_records("source_records"),
        "source_script_revisions": source_revisions,
        "source_assignment_status": "episode" if numbers else "unassigned",
        "script_dependency_status": "current",
        "script_stale_reason": None,
    }


async def rebind_project_asset_scopes(
    session: AsyncSession,
    project_id: int,
    settings: dict[str, Any],
    impacts: list[str],
) -> dict[str, Any]:
    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project_id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )
    narrative_scope = build_narrative_asset_scope(
        settings,
        [
            {
                "episode_id": episode.id,
                "episode_number": episode.number,
                "script_revision": episode.script_revision,
            }
            for episode in episodes
        ],
    )
    rows = (
        await session.execute(
            select(Asset, ProjectAssetLink)
            .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
            .where(ProjectAssetLink.project_id == project_id)
        )
    ).all()
    rebound_ids: list[int] = []
    stale_ids: list[int] = []
    reuse_changed = "character_reuse" in impacts
    invalid_spec = narrative_scope.get("narrative_status") != "confirmed"
    for asset, link in rows:
        data = dict(link.production_data or {})
        stored = dict(data.get("script_scope") or {})
        if stored.get("source_kind") != "script_asset_breakdown":
            continue
        requirement_type = str(
            stored.get("requirement_type") or asset.asset_type
        )
        rebound = {
            **stored,
            **candidate_narrative_scope(
                [int(value) for value in stored.get("episode_numbers") or []],
                narrative_scope,
            ),
        }
        requires_review = invalid_spec or (
            reuse_changed
            and requirement_type in {"character", "costume", "character_voice"}
        )
        rebound["narrative_dependency_status"] = (
            "stale" if requires_review else "current"
        )
        rebound["narrative_stale_reason"] = (
            "角色复用策略已变化，请重新核对人物、造型和角色声音资产"
            if reuse_changed and requires_review
            else "剧集结构规格尚未确认，请确认后重新核对资产范围"
            if invalid_spec
            else None
        )
        data["script_scope"] = rebound
        if link.production_data != data:
            link.production_data = data
            link.production_revision += 1
        if asset.project_id == project_id:
            attributes = dict(asset.attributes or {})
            attributes.update(rebound)
            asset.attributes = attributes
        rebound_ids.append(asset.id)
        if requires_review:
            stale_ids.append(asset.id)
    return {
        "narrative_scope": narrative_scope,
        "rebound_asset_ids": sorted(set(rebound_ids)),
        "stale_asset_ids": sorted(set(stale_ids)),
    }


def rebind_asset_breakdown_state(
    settings: dict[str, Any],
    result: dict[str, Any],
    impacts: list[str],
) -> dict[str, Any]:
    updated = dict(settings)
    breakdown = dict(updated.get("asset_breakdown") or {})
    if not breakdown:
        return updated
    narrative_scope = dict(result.get("narrative_scope") or {})
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    for candidate in candidates:
        candidate["narrative_scope"] = candidate_narrative_scope(
            [int(value) for value in candidate.get("episode_numbers") or []],
            narrative_scope,
        )
    breakdown.update(
        {
            "candidates": candidates,
            "narrative_scope": narrative_scope,
            "narrative_spec_revision": narrative_scope.get("narrative_spec_revision", 0),
            "narrative_spec_impacts": impacts,
            "narrative_rebound_asset_ids": result.get("rebound_asset_ids", []),
            "narrative_stale_asset_ids": result.get("stale_asset_ids", []),
        }
    )
    requires_review = bool(
        result.get("stale_asset_ids")
        or narrative_scope.get("narrative_status") != "confirmed"
    )
    in_flight = breakdown.get("status") in {"running", "awaiting_confirmation"}
    if in_flight or requires_review:
        breakdown.update(
            {
                "status": "stale",
                "completed": False,
                "stale_reason": (
                    "剧集结构规格已变化，请按当前规格重新执行资产拆解"
                ),
            }
        )
    updated["asset_breakdown"] = breakdown
    return updated
