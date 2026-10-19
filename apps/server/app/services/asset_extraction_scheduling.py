"""Source-range dependencies, independent of worker completion order."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CreationSession
from app.services.creation_breakdown_planning import MAX_SCRIPT_CHARS_PER_WORK_UNIT


def planning_budget(defaults: dict[str, Any]) -> dict[str, Any]:
    ceiling = dict(defaults.get("_text_execution") or {}).get("max_output_tokens")
    # A conservative workload estimate, not a tokenizer or a completeness guarantee.
    budget = (
        min(MAX_SCRIPT_CHARS_PER_WORK_UNIT, max(256, ceiling // 2))
        if ceiling
        else MAX_SCRIPT_CHARS_PER_WORK_UNIT
    )
    return {
        "max_script_chars": budget,
        "configured_output_tokens": ceiling,
        "estimate_kind": "conservative_char_budget",
        "max_episodes": 2,
    }


async def lock_breakdown_session(session: AsyncSession, item: CreationSession) -> None:
    if session.get_bind().dialect.name == "sqlite":
        # SQLite has a database-wide writer reservation rather than row locks.
        await session.execute(
            update(CreationSession)
            .where(CreationSession.id == item.id)
            .values(settings=CreationSession.settings)
            .execution_options(synchronize_session=False)
        )
    else:
        await session.execute(
            select(CreationSession.id).where(CreationSession.id == item.id).with_for_update()
        )
    await session.refresh(item, attribute_names=["settings"])


def overlapping_scopes(
    audio: dict[str, Any], children: list[Any], active_keys: list[str]
) -> list[str]:
    ranges = {
        int(row["episode_number"]): (int(row["start"]), int(row["end"]))
        for row in audio.get("source_ranges") or []
    }
    numbers = {int(value) for value in audio.get("episode_numbers") or []}
    dependencies = set()
    for child in children:
        params = dict((child.payload or {}).get("parameters") or {})
        key = params.get("scope_key")
        if key not in active_keys or params.get("requirement_group") != "visual":
            continue
        child_ranges = {
            int(row["episode_number"]): (int(row["start"]), int(row["end"]))
            for row in params.get("source_ranges") or []
        }
        for number in numbers.intersection(params.get("episode_numbers") or []):
            left, right = ranges.get(number), child_ranges.get(number)
            if left is None or right is None or max(left[0], right[0]) < min(left[1], right[1]):
                dependencies.add(key)
    return sorted(dependencies)
