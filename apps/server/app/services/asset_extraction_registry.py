"""Deterministic, source-preserving identity projection for extraction V2."""

import hashlib
import json
from copy import deepcopy
from typing import Any

from app.services.asset_identity_variants import (
    VARIANT_FIELDS,
    descriptor,
    flag_name_uncertainty,
    resolve_variants,
    review_identity,
    time_key,
)
from app.services.creation_breakdown_planning import (
    REQUIREMENT_RESULT_KEYS,
    normalized_asset_key,
)


def _key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _marker(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _formal_kind(asset: dict[str, Any]) -> str:
    attrs = dict(asset.get("attributes") or {})
    return (
        (attrs.get("audio_purpose") or "character_voice")
        if asset.get("asset_type") == "voice"
        else attrs.get("requirement_type", asset.get("asset_type"))
    )


def _narrative_identity(row, context):
    from app.services.creation_asset_scope import candidate_narrative_scope
    attrs = row.get("attributes") or {}
    scope = row.get("narrative_scope") or attrs
    if not scope.get("narrative_structure"):
        scope = candidate_narrative_scope(row.get("episode_numbers") or attrs.get("episode_numbers") or [],
                                         context.get("narrative_scope"))
    structure = scope.get("narrative_structure")
    if structure in (None, "continuous"):
        return ""
    units = sorted(scope.get("unit_ids") or [])
    episodes = sorted(row.get("episode_numbers") or attrs.get("episode_numbers") or [])
    return _marker([structure, units or episodes])


def unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for _, row in sorted({_marker(row): row for row in records}.items())]


def _references(context: dict[str, Any], existing: list[dict[str, Any]]) -> dict:
    """Only confirmed reference aliases, never model-proposed aliases, join names."""
    refs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    bible = dict(context.get("confirmed_story_bible") or {})
    content = dict(bible.get("content") or {})
    rows = [
        {"kind": "character", "name": row.get("name"), "aliases": row.get("aliases") or []}
        for row in content.get("characters") or []
        if isinstance(row, dict)
    ]
    rows.extend(
        {
            "kind": (dict(row.get("attributes") or {}).get("audio_purpose") or "character_voice")
            if row.get("asset_type") == "voice"
            else dict(row.get("attributes") or {}).get("requirement_type", row.get("asset_type")),
            "name": row.get("name"),
            "aliases": dict(row.get("attributes") or {}).get("aliases") or [],
        }
        for row in existing
    )
    canonical_keys = {(row["kind"], _key(row["name"])) for row in rows if _key(row["name"])}
    for row in rows:
        canonical = _key(row["name"])
        if not canonical:
            continue
        for label in [row["name"], *row["aliases"]]:
            if _key(label):
                if _key(label) != canonical and (row["kind"], _key(label)) in canonical_keys:
                    continue
                refs.setdefault((row["kind"], _key(label)), {})[canonical] = row
    return refs


def _resolve_name(kind: str, name: str, refs: dict) -> tuple[str, bool]:
    owners = refs.get((kind, _key(name)), {})
    if len(owners) == 1:
        return str(next(iter(owners.values()))["name"]).strip(), False
    return name.strip(), len(owners) > 1


def merge_entries(rows: list[dict[str, Any]], *, kind: str | None = None) -> dict[str, Any]:
    rows = sorted(rows, key=_marker)
    result = deepcopy(rows[0])
    result["aliases"] = sorted(
        {
            str(value).strip()
            for row in rows
            for value in [row["name"], *row.get("aliases", [])]
            if str(value).strip()
        }
    )
    result["episode_numbers"] = sorted(
        {number for row in rows for number in row.get("episode_numbers", [])}
    )
    result["usage_records"] = unique_records(
        [record for row in rows for record in row.get("usage_records", [])]
    )
    result["description"] = min(
        (str(row.get("description") or "") for row in rows), key=lambda value: (-len(value), value)
    )
    attributes: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    for field in sorted({field for row in rows for field in row.get("attributes", {})}):
        values = [
            row["attributes"][field]
            for row in rows
            if row.get("attributes", {}).get(field) not in (None, "", [])
        ]
        if not values:
            continue
        if field == "needs_review":
            attributes[field] = any(values)
        elif all(isinstance(value, list) for value in values):
            attributes[field] = [
                value
                for _, value in sorted(
                    {_marker(value): value for array in values for value in array}.items()
                )
            ]
        else:
            distinct = {_marker(value): value for value in values}
            if len(distinct) == 1:
                attributes[field] = next(iter(distinct.values()))
            else:
                combined = None
                if kind == "scene" and field == "time" and len({time_key(value) for value in values}) == 1:
                    combined = time_key(values[0])
                if kind == "scene" and field == "base_scene_name" and all(
                    "base_scene_name" not in descriptor(kind, row)[1] for row in rows
                ):
                    combined = descriptor(kind, result)[0]
                if kind in {"character", "prop"} and field == "appearance" and all(isinstance(value, str) for value in distinct.values()):
                    from app.services.asset_appearance_facts import merge_complementary_appearance
                    combined = merge_complementary_appearance(distinct.values(), kind=kind)
                if field in {"extraction_identity", "asset_key"}:
                    # Identity is assigned after projection, never voted from model fields.
                    attributes[field] = result["attributes"][field]
                elif combined is not None:
                    attributes[field] = combined
                else:
                    conflicts[field] = [value for _, value in sorted(distinct.items())]
    if conflicts:
        attributes["identity_fact_conflicts"] = conflicts
        attributes["needs_review"] = True
    result["attributes"] = attributes
    return result


def project_registry(
    breakdown: dict[str, Any],
    *,
    context: dict[str, Any],
    existing: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild from immutable batches so completion order and replays cannot win."""
    refs = _references(context, existing)
    formal_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    formal_by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for asset in existing:
        attrs = dict(asset.get("attributes") or {})
        kind = _formal_kind(asset)
        if attrs.get("extraction_identity"):
            formal_by_identity.setdefault((kind, attrs["extraction_identity"]), []).append(asset)
        else:
            formal_by_name.setdefault((kind, _key(asset.get("name"))), []).append(asset)
    grouped: dict[str, list[dict[str, Any]]] = {}
    named: dict[tuple[str, str, str], list[dict]] = {}
    from app.services.creation_finalize_service import _normalize_asset_classification

    for source_kind, group in REQUIREMENT_RESULT_KEYS.items():
        for original in breakdown.get(group) or []:
            row = deepcopy(original)
            kind, _, aliases, attrs = _normalize_asset_classification(
                source_kind,
                "voice"
                if source_kind in {"character_voice", "music", "ambience", "sound_effect"}
                else source_kind,
                row,
            )
            row["aliases"] = aliases
            row["attributes"] = attrs
            attrs = row["attributes"]
            canonical, ambiguous = _resolve_name(kind, row["name"], refs)
            row["aliases"] = list(dict.fromkeys([row["name"], *row.get("aliases", [])]))
            row["name"] = canonical
            for field in VARIANT_FIELDS.get(kind, ()):
                if attrs.get(field):
                    value = attrs[field]
                    if field in {"character_name", "owner"}:
                        value, owner_ambiguous = _resolve_name("character", value, refs)
                        ambiguous = ambiguous or owner_ambiguous
                        attrs[field] = value
            identity_name, _ = descriptor(kind, row)
            scope = _narrative_identity(row, context) if kind in {"scene", "prop", "costume"} else ""
            named.setdefault((kind, _key(identity_name), scope), []).append(row)
            if ambiguous:
                review_identity(attrs, "Confirmed aliases have multiple owners")
    formal_named: dict[tuple[str, str, str], list[dict]] = {}
    for asset in existing:
        kind = _formal_kind(asset)
        name, _ = descriptor(kind, asset)
        canonical, _ = _resolve_name(kind, name, refs)
        canonical, _ = descriptor(kind, {**asset, "name": canonical})
        scope = _narrative_identity(asset, context) if kind in {"scene", "prop", "costume"} else ""
        formal_named.setdefault((kind, _key(canonical), scope), []).append(asset)
    for (kind, _, scope), named_rows in sorted(named.items()):
        formal = formal_named.get((kind, _key(descriptor(kind, named_rows[0])[0]), scope), [])
        resolutions = resolve_variants(kind, named_rows, formal) if kind in {"prop", "scene", "costume"} else [
            (row, {}, None) for row in named_rows
        ]
        for row, variants, resolved_formal in resolutions:
            if scope:
                variants = {**variants, "narrative_scope": scope}
            attrs = row["attributes"]
            canonical = descriptor(kind, row)[0]
            if kind == "character" and attrs.get("character_form") in {"fixed_group", "crowd"}:
                variants = {field: attrs.get(field) for field in ("character_form", "group_label", "group_size")}
                if attrs.get("extraction_contract_version") == 3:
                    from app.services.asset_character_identity import scope_keys

                    variants["source_scope"] = scope_keys(row)
            identity_name = (
                _key(attrs["linked_asset_key"])
                if kind == "character_voice" and attrs.get("linked_asset_key")
                else _key(canonical)
            )
            identity = _marker([kind, identity_name, variants])
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if resolved_formal and resolved_formal.get("attributes", {}).get("extraction_identity"):
                digest = resolved_formal["attributes"]["extraction_identity"]
                identity = _marker([kind, "formal", digest])
            attrs["extraction_identity"] = digest
            # Display-name whitespace cannot collapse two different identities.
            attrs["asset_key"] = normalized_asset_key(kind, identity_name) + (
                f":variant:{digest[:24]}" if variants else f":identity:{digest[:24]}"
            )
            formal_matches = ([resolved_formal] if resolved_formal else []) or formal_by_identity.get((kind, digest), []) or (
                formal_by_name.get((kind, _key(canonical)), []) if not variants else []
            )
            if len(formal_matches) == 1 and formal_matches[0].get("attributes", {}).get(
                "asset_key"
            ):
                attrs["asset_key"] = formal_matches[0]["attributes"]["asset_key"]
            grouped.setdefault(identity, []).append(row)
    result = {
        "reply": breakdown.get("reply", ""),
        **{group: [] for group in REQUIREMENT_RESULT_KEYS.values()},
    }
    for identity, rows in sorted(grouped.items()):
        kind = json.loads(identity)[0]
        row = merge_entries(rows, kind=kind)
        result[REQUIREMENT_RESULT_KEYS[kind]].append(row)
    for kind, group in REQUIREMENT_RESULT_KEYS.items():
        flag_name_uncertainty(kind, result[group])
    return result


def candidate_id(attributes: dict[str, Any]) -> int:
    # 52 bits remain exact in JavaScript and do not renumber completed candidates.
    return int(attributes["extraction_identity"][:13], 16) + 1
