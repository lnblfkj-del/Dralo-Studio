"""Narrative-aware context rules for episode screenplay generation."""

from typing import Any

from app.services.narrative_spec_service import normalize_narrative_spec


def known_character_names(bible: dict[str, Any]) -> list[str]:
    return [
        str(item.get("name") or "").strip()
        for item in bible.get("characters", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def episode_generation_context(
    narrative_spec: dict[str, Any] | None,
    episode_number: int,
) -> dict[str, Any]:
    """Resolve the dependency and closure rules for one episode."""

    spec = normalize_narrative_spec(narrative_spec)
    structure = str(spec.get("structure") or "continuous")
    unit = next(
        (
            item
            for item in list(spec.get("units") or [])
            if int(item.get("episode_start") or 0)
            <= episode_number
            <= int(item.get("episode_end") or 0)
        ),
        None,
    )
    unit_continuity = str((unit or {}).get("continuity") or "")

    requires_previous = structure == "continuous" and episode_number > 1
    context_strategy = "continuous_series"
    episode_closed = False
    if structure == "independent":
        requires_previous = False
        context_strategy = "independent_episode"
        episode_closed = True
    elif structure == "hybrid":
        requires_previous = False
        context_strategy = "hybrid_episode"
        episode_closed = True
    elif structure == "unit":
        requires_previous = (
            unit_continuity == "continuous"
            and episode_number > int((unit or {}).get("episode_start") or episode_number)
        )
        context_strategy = (
            "continuous_unit" if requires_previous else "independent_unit_episode"
        )
        episode_closed = unit_continuity != "continuous"

    return {
        "structure": structure,
        "unit": unit,
        "unit_continuity": unit_continuity or None,
        "requires_previous": requires_previous,
        "include_recent_continuity": requires_previous,
        "episode_closed": episode_closed,
        "context_strategy": context_strategy,
    }


def episode_generation_instruction(context: dict[str, Any]) -> str:
    """Return explicit model instructions matching the resolved context policy."""

    structure = context["structure"]
    if context["requires_previous"]:
        scope = "the current unit" if structure == "unit" else "the series"
        return (
            f"This episode is part of a continuous {scope}. Treat the supplied previous "
            "episode script as the only direct opening handoff. Preserve established facts "
            "and do not repeat events that already finished."
        )
    if structure == "hybrid":
        return (
            "Make this episode understandable and dramatically complete on its own while "
            "allowing confirmed long-term arcs to advance. Do not invent a mandatory handoff "
            "from the previous episode or inherit its temporary incident and props."
        )
    if structure == "unit":
        return (
            "Use only the current unit and confirmed shared setting. This episode does not "
            "require the preceding episode script; do not inherit temporary events from a "
            "different unit. Give the episode a complete dramatic arc."
        )
    return (
        "Treat this as an independent episode. Use the shared setting and current outline, "
        "but do not assume or invent a direct handoff from another episode. Resolve the main "
        "episode conflict within this episode."
    )
