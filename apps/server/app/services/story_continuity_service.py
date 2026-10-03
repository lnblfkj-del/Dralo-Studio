"""Deterministic C1 continuity extraction and versioned persistence."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Episode
from app.models.continuity import (
    CONTINUITY_CONFIRMATION_CONFIRMED,
    CONTINUITY_CONFIRMATION_DRAFT,
    CONTINUITY_STATUS_ACTIVE,
    CONTINUITY_STATUS_SUPERSEDED,
    StoryContinuityFact,
)

ENDING_EXCERPT_LIMIT = 1600


def _clean(value: str | None) -> str:
    return re.sub(r"\r\n?", "\n", value or "").strip()


def _ending_excerpt(script: str) -> str:
    marker_matches = list(re.finditer(r"[【\[]?集尾钩子[】\]]?", script))
    if marker_matches:
        return script[marker_matches[-1].start():].strip()[-ENDING_EXCERPT_LIMIT:]
    return script[-ENDING_EXCERPT_LIMIT:].strip()


def build_episode_continuity_records(
    episode: Episode,
    *,
    confirmation_status: str,
    known_character_names: list[str] | None = None,
) -> list[dict]:
    """Build evidence-only records without asking a model to infer facts."""

    if confirmation_status not in {
        CONTINUITY_CONFIRMATION_DRAFT,
        CONTINUITY_CONFIRMATION_CONFIRMED,
    }:
        raise ValueError("confirmation_status must be draft or confirmed")
    script = _clean(episode.script)
    synopsis = _clean(episode.synopsis)
    base = f"episode:{episode.number}"
    records: list[dict] = []
    if synopsis:
        records.append({
            "fact_type": "episode_event",
            "fact_key": f"{base}:summary",
            "value": {
                "episode_number": episode.number,
                "title": episode.title,
                "summary": synopsis,
            },
            "evidence_excerpt": synopsis,
        })
    if script:
        ending = _ending_excerpt(script)
        records.append({
            "fact_type": "episode_handoff",
            "fact_key": f"{base}:ending",
            "value": {"episode_number": episode.number, "ending_excerpt": ending},
            "evidence_excerpt": ending,
        })
        if re.search(r"集尾钩子", ending):
            records.append({
                "fact_type": "open_thread",
                "fact_key": f"{base}:cliffhanger",
                "value": {"episode_number": episode.number, "thread": ending},
                "evidence_excerpt": ending,
            })
        for name in dict.fromkeys(known_character_names or []):
            if name and name in script:
                records.append({
                    "fact_type": "character_presence",
                    "fact_key": f"{base}:character:{name}",
                    "value": {"episode_number": episode.number, "name": name},
                    "evidence_excerpt": name,
                })
    return records


async def sync_episode_continuity_records(
    session: AsyncSession,
    episode: Episode,
    *,
    confirmation_status: str,
    known_character_names: list[str] | None = None,
    continuity_update: dict | None = None,
) -> list[StoryContinuityFact]:
    """Persist one script revision idempotently and retain superseded history."""

    source_ref = f"episode:{episode.id}:script"
    records = build_episode_continuity_records(
        episode,
        confirmation_status=confirmation_status,
        known_character_names=known_character_names,
    )
    prior = list((await session.scalars(select(StoryContinuityFact).where(
        StoryContinuityFact.project_id == episode.project_id,
        StoryContinuityFact.source_ref == source_ref,
    ))).all())
    current = {
        (item.fact_type, item.fact_key): item
        for item in prior
        if item.source_revision == episode.script_revision
    }
    if continuity_update is not None:
        for field in ("end_state", "character_changes", "prop_changes", "resolved_hooks", "new_hooks"):
            values = continuity_update.get(field) or []
            if isinstance(values, str):
                values = [values]
            for index, text in enumerate(values):
                records.append({
                    "fact_type": "generated_" + field,
                    "fact_key": f"episode:{episode.number}:{field}:{index}",
                    "value": {"text": text, "episode_number": episode.number, "basis": "model_script_update"},
                    "evidence_excerpt": "",
                })
    else:
        # A confirmation of the same script revision retains its generated facts.
        records.extend({"fact_type": row.fact_type, "fact_key": row.fact_key,
                        "value": row.value, "evidence_excerpt": row.evidence_excerpt}
                       for row in current.values() if row.fact_type.startswith("generated_"))
    for item in prior:
        if item.source_revision != episode.script_revision and item.status == CONTINUITY_STATUS_ACTIVE:
            item.status = CONTINUITY_STATUS_SUPERSEDED

    kept: list[StoryContinuityFact] = []
    record_keys = {(item["fact_type"], item["fact_key"]) for item in records}
    for key, item in current.items():
        if key not in record_keys:
            item.status = CONTINUITY_STATUS_SUPERSEDED
    for record in records:
        key = (record["fact_type"], record["fact_key"])
        item = current.get(key)
        if item is None:
            item = StoryContinuityFact(
                project_id=episode.project_id,
                owner_id=episode.owner_id,
                source_episode_id=episode.id,
                source_ref=source_ref,
                source_kind="episode_script",
                source_revision=episode.script_revision,
                confirmation_status=confirmation_status,
                **record,
            )
            session.add(item)
        else:
            item.confirmation_status = confirmation_status
            item.value = record["value"]
            item.evidence_excerpt = record["evidence_excerpt"]
            item.status = CONTINUITY_STATUS_ACTIVE
        kept.append(item)
    await session.flush()
    return kept
