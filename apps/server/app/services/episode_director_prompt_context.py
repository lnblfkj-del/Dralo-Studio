"""Read-only, bounded context from the director's frozen input."""

from copy import deepcopy
from typing import Any

from app.services.episode_director_skill_projection import project_rules

NEIGHBOR_TEXT_BUDGET = 1600
NEIGHBOR_LINE_LIMIT = 6


def frozen_skill_rules(
    execution: dict[str, Any], *, stage: str | None = None
) -> list[dict[str, Any]]:
    if stage is not None and execution.get("skill_rule_projection") is not None:
        stages = execution["skill_rule_projection"]["stages"]
        if stage not in stages:
            raise ValueError(f"Missing frozen director stage: {stage}")
        return deepcopy(stages[stage])
    rules = [
        {
            "key": item["key"],
            "version": item["version"],
            "instruction": (item.get("snapshot") or {}).get("instruction", ""),
        }
        for item in execution.get("skill_bundle") or []
    ]
    return project_rules(rules, stage) if stage is not None else rules


def _boundary_lines(rows: list[dict[str, Any]], *, previous: bool) -> dict[str, Any]:
    selected = []
    remaining = NEIGHBOR_TEXT_BUDGET
    ordered = reversed(rows) if previous else iter(rows)
    for row in ordered:
        if len(selected) >= NEIGHBOR_LINE_LIMIT or remaining <= 0:
            break
        text = str(row["text"])
        excerpt = text[-remaining:] if previous else text[:remaining]
        selected.append({"line": row["line"], "text": excerpt, "excerpt": excerpt != text})
        remaining -= len(excerpt)
    if previous:
        selected.reverse()
    return {
        "source_lines": selected,
        "available_lines": len(rows),
        "omitted_lines": len(rows) - len(selected),
    }


def segment_handoff_context(
    execution: dict[str, Any], outline: dict[str, Any], segment: dict[str, Any]
) -> dict[str, Any]:
    snapshot = execution["input"]
    segments = outline["segments"]
    position = next(index for index, item in enumerate(segments) if item["key"] == segment["key"])
    sources = {int(item["shot_id"]): item for item in outline["shots"]}
    existing = {int(item["shot_id"]): item for item in snapshot.get("shots") or []}
    scene_id = sources[int(segment["shot_ids"][0])]["scene_id"]

    def neighbor(index: int, *, previous: bool) -> dict[str, Any] | None:
        if index < 0 or index >= len(segments):
            return None
        item = segments[index]
        ids = item["shot_ids"]
        line_ids = {
            line for shot_id in ids for line in sources[int(shot_id)].get("source_lines") or []
        }
        rows = [row for row in snapshot.get("source_lines") or [] if row["line"] in line_ids]
        # Legacy plans may lack line mappings; use only their frozen boundary shots.
        boundary_ids = ids[-2:] if previous else ids[:2]
        boundary_shots = (
            [
                {
                    "shot_id": shot_id,
                    **{
                        field: str(existing[int(shot_id)].get(field) or "")[:400]
                        for field in ("action", "dialogue", "audio_note")
                    },
                }
                for shot_id in boundary_ids
                if int(shot_id) in existing
            ]
            if not rows
            else []
        )
        return {
            "segment_key": item["key"],
            "same_scene": sources[int(ids[0])]["scene_id"] == scene_id,
            "source_kind": "frozen_source_not_generated_result",
            **_boundary_lines(rows, previous=previous),
            "boundary_shot_excerpts": boundary_shots,
        }

    return {
        "source_script_revision": execution.get("source_script_revision"),
        "previous": neighbor(position - 1, previous=True),
        "next": neighbor(position + 1, previous=False),
    }
