"""P1 project-source normalization and immutable provenance helpers."""

from typing import Any

from app.services.narrative_spec_service import normalize_narrative_spec

PREFERENCE_FIELDS = (
    "episode_count",
    "episode_duration",
    "market",
    "style_id",
    "custom_style",
    "aspect_ratio",
)

IMMUTABLE_SOURCE_FIELDS = frozenset(
    {
        "source_type",
        "brief",
        "reference_name",
        "reference_text",
        "import_session_id",
        "import_analysis",
        "market_research_run_id",
        "market_idea_index",
        "market_source_ids",
        "market_idea_title",
        "market_idea_snapshot",
        "market_source_snapshot",
    }
)


def merge_creation_settings(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge editable preferences without allowing source provenance to drift."""

    current = dict(existing or {})
    merged = {**current, **dict(incoming or {})}
    for key in IMMUTABLE_SOURCE_FIELDS:
        if key in current:
            merged[key] = current[key]
    merged["narrative_spec"] = normalize_narrative_spec(
        merged.get("narrative_spec"),
        episode_count=merged.get("episode_count", 10),
        episode_duration=merged.get("episode_duration", 90),
    )
    return merged


def synchronize_source_fields(
    target: dict[str, Any] | None,
    canonical: dict[str, Any] | None,
) -> dict[str, Any]:
    """Copy canonical project provenance into an older or partial session."""

    merged = dict(target or {})
    canonical_data = dict(canonical or {})
    for key in IMMUTABLE_SOURCE_FIELDS:
        if key in canonical_data:
            merged[key] = canonical_data[key]
    if canonical_data.get("reference_text") and merged.get("reference_project_id"):
        merged["reference_text"] = ""
    for key in PREFERENCE_FIELDS:
        if key in canonical_data:
            merged[key] = canonical_data[key]
    return merged


def summarize_project_source(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Return one stable source contract for upload, original, market and blank projects."""

    data = dict(settings or {})
    raw_type = str(data.get("source_type") or "blank")
    run_id = data.get("market_research_run_id")
    if run_id is not None:
        kind = "market"
    elif raw_type == "upload":
        kind = "upload"
    elif raw_type == "blank":
        kind = "blank"
    else:
        # Legacy generic `idea` and current `write` are both original creation.
        kind = "write"

    analysis = data.get("import_analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    source_ids = data.get("market_source_ids")
    if not isinstance(source_ids, list):
        source_ids = []
    source_snapshot = data.get("market_source_snapshot")
    if not isinstance(source_snapshot, list):
        source_snapshot = []

    return {
        "kind": kind,
        "raw_source_type": raw_type,
        "source_preserved": bool(
            data.get("import_session_id") or data.get("reference_text") or data.get("brief") or data.get("market_idea_snapshot")
        ),
        "reference_name": str(data.get("reference_name") or "") or None,
        "original_char_count": int(analysis.get("original_char_count") or 0),
        "import_mode": str(analysis.get("mode") or "") or None,
        "import_confidence": str(analysis.get("confidence") or "") or None,
        "market_research_run_id": int(run_id) if run_id is not None else None,
        "market_idea_index": (
            int(data["market_idea_index"]) if data.get("market_idea_index") is not None else None
        ),
        "market_idea_title": str(data.get("market_idea_title") or "") or None,
        "market_source_ids": [int(item) for item in source_ids if isinstance(item, int)],
        "market_sources": [dict(item) for item in source_snapshot if isinstance(item, dict)],
    }
