"""R3 production-context contract shared by upload/original creation flows.

The context is a compact, immutable description of where production facts came
from. The original source remains in the session/project; this snapshot only
records hashes, revisions, locators and the confirmed story-bible payload that
downstream extraction is allowed to use.
"""

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CreationArtifact, CreationSession, Episode
from app.services.creation_asset_scope import build_narrative_asset_scope
from app.services.script_source_snapshot import build_script_snapshot


def _source_kind(item: CreationSession) -> str:
    settings = dict(item.settings or {})
    if settings.get("market_research_run_id") is not None:
        return "market"
    if str(settings.get("source_type") or "").lower() == "upload":
        return "upload"
    return "original"


def _locator(script: str, source_text: str, number: int) -> dict[str, Any]:
    """Return a stable human-readable locator and best-effort source range."""

    if source_text and script:
        start = source_text.find(script)
        if start >= 0:
            return {
                "kind": "reference_text",
                "label": f"原文第 {number} 集",
                "start": start,
                "end": start + len(script),
            }
    return {"kind": "episode_script", "label": f"正式正文第 {number} 集"}


async def build_production_context(
    session: AsyncSession,
    item: CreationSession,
    episodes: list[Episode],
    story_bible: CreationArtifact | None,
) -> dict[str, Any]:
    """Build the R3 context passed to production extraction jobs.

    No model inference happens here. Missing or ambiguous facts remain marked
    for review and are never filled with guessed values.
    """

    settings = dict(item.settings or {})
    from app.services.source_index_service import resolve_source_text
    reference_text = await resolve_source_text(session, item)
    source_sha256 = hashlib.sha256(reference_text.encode("utf-8")).hexdigest() if reference_text else None
    snapshot = build_script_snapshot(episodes)
    episode_sources = []
    source_records = []
    for episode in episodes:
        script = str(episode.script or "")
        locator = _locator(script, reference_text, episode.number)
        episode_sources.append({
            "episode_id": episode.id,
            # Use an explicit name here so callers cannot confuse provenance
            # metadata with the numbered episode list in an AI result.
            "episode_number": episode.number,
            "title": episode.title,
            "script_revision": episode.script_revision,
            "finalized_script_revision": episode.finalized_script_revision,
            "duration_seconds": episode.duration_estimate,
            "locator": locator,
            "source_kind": "formal_script" if script else "reference_text",
        })
        source_records.append({
            "kind": "script" if script else "upload",
            "episode_id": episode.id,
            "script_revision": episode.script_revision,
            "locator": locator["label"],
            "note": "事实必须回到该集原文核对；不明确内容标记待核对。",
        })

    bible_record: dict[str, Any] | None = None
    if story_bible is not None:
        bible_record = {
            "artifact_id": story_bible.id,
            "version": story_bible.version,
            "revision": story_bible.revision,
            "status": story_bible.status,
            "content": story_bible.content,
            "source": {
                "kind": "confirmed_story_bible",
                "locator": f"story_bible:v{story_bible.version}",
                "note": "仅作为已确认设定参考；与正文冲突时以正文事实为准。",
            },
        }

    narrative_scope = build_narrative_asset_scope(settings, episode_sources)
    from app.services.creation_breakdown_validation import explicit_dialogue_appearances

    characters = (story_bible.content or {}).get("characters", []) if story_bible else []
    names = list(dict.fromkeys(str(row.get("name") or "").strip()
                               for row in characters if isinstance(row, dict)))
    appearances = [record for episode in episodes
                   for record in explicit_dialogue_appearances(
                       str(episode.script or ""), episode.number, names)]
    return {
        "character_appearances": appearances,
        "schema_version": "r3.production_context.v1",
        "source_kind": _source_kind(item),
        "source_name": str(settings.get("reference_name") or item.title or "") or None,
        "source_preserved": bool(reference_text),
        "source_text_sha256": source_sha256,
        "episode_count": len(episodes),
        "source_script_revisions": snapshot["source_script_revisions"],
        "input_fingerprint": snapshot["input_fingerprint"],
        "episodes": episode_sources,
        "narrative_scope": narrative_scope,
        "story_source": {
            "artifact_id": story_bible.id,
            "version": story_bible.version,
            "revision": story_bible.revision,
        } if story_bible is not None else {},
        "confirmed_story_bible": bible_record,
        "unresolved_policy": "保留中英文原名、别名、服装、场景、声音与原文定位；无法确认的资料标记 needs_review，不修改原文。",
        "source_records": source_records,
    }
