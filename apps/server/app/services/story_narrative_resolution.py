"""Resolve auto structure on story approval, without another model request."""

import re
from collections import Counter
from typing import Any

from pydantic import ValidationError

from app.core.errors import ConflictError
from app.schemas.narrative_spec import NarrativeSpec
from app.services.narrative_spec_service import narrative_spec_from_settings, with_narrative_spec
from app.services.story_bible_structure_review import validate_story_bible_structure


def resolve_story_spec(settings: dict[str, Any], story: dict[str, Any], brief: str = "") -> dict[str, Any]:
    current = narrative_spec_from_settings(settings)
    if current["status"] == "confirmed":
        validate_story_bible_structure(story, settings)
        return dict(settings)
    candidate = dict(current)
    if current["structure"]:
        # Explicit choices and imported classifications are approved with the story.
        if current["source"] not in {"manual", "imported"}:
            raise _needs_choice()
    else:
        recommendation = story.get("structure_recommendation") or {}
        structure = None
        units = []
        if recommendation:
            if (not isinstance(recommendation, dict) or recommendation.get("confidence") != "high"
                    or not isinstance(recommendation.get("reason"), str) or not recommendation["reason"].strip()):
                raise _needs_choice()
            structure = recommendation.get("structure")
            # Models may attach arc notes to non-unit stories; only units own ranges.
            units = (recommendation.get("units") or []) if structure == "unit" else []
        else:
            structure = _legacy_structure(story, brief, current["episode_count"])
        if structure is None:
            raise _needs_choice()
        candidate.update(source="automatic", structure=structure, units=units,
                         character_reuse={"continuous": "fixed", "independent": "per_episode", "unit": "rotating", "hybrid": "rotating"}.get(structure))
    candidate["status"] = "confirmed"
    try:
        NarrativeSpec.model_validate(candidate)
        resolved = with_narrative_spec(settings, candidate, expected_revision=current["revision"])
    except (ValidationError, TypeError, ValueError) as exc:
        raise _needs_choice() from exc
    validate_story_bible_structure(story, resolved)
    return resolved


def _needs_choice() -> ConflictError:
    return ConflictError("无法可靠判断剧集结构，请选择连续故事、单集独立或单元故事后保存。已有故事无需重新生成。",
                         details={"needs_structure_choice": True})


def _legacy_structure(story: dict[str, Any], brief: str, count: int) -> str | None:
    text = f"{brief}\n{story.get('logline', '')}"
    choices = [value for value, terms in {
        "continuous": ("连续故事", "主线贯穿全剧"),
        "independent": ("单集独立", "每集独立闭环"),
        "hybrid": ("独立集+长线", "独立集＋长线", "单集闭环加长期主线"),
    }.items() if any(term in text for term in terms)]
    if len(choices) == 1:
        return choices[0]
    if choices or any(term in text for term in ("单元故事", "单元剧", "每集一个独立", "每集一个新的")):
        return None
    # Old staged stories need both full phase coverage and explicit cross-event links.
    phases = story.get("phase_plan") or []
    events = story.get("event_timeline") or []
    try:
        coverage = [n for phase in phases for n in range(phase["episode_range"]["start"], phase["episode_range"]["end"] + 1)]
        hints = [event.get("episode_hint") for event in events]
        shared = Counter(cid for event in events for cid in set(event.get("character_ids") or []))
        links = sum(bool(re.search(r"上一集|承接上集|前期建立|事件后|失去.{1,20}后|随着.{1,20}深入|后续的", event.get("summary", ""))) for event in events)
        if count > 1 and coverage == list(range(1, count + 1)) and hints == coverage and max(shared.values(), default=0) == count and links >= 2:
            return "continuous"
    except (KeyError, TypeError, ValueError):
        pass
    return None
