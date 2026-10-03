"""Deterministic workload planning for script asset extraction."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from app.models import PROTOCOL_GOOGLE_GEMINI

VISUAL_REQUIREMENT_TYPES = ("character", "costume", "scene", "prop")
AUDIO_REQUIREMENT_TYPES = ("character_voice", "music", "ambience", "sound_effect")
REQUIREMENT_RESULT_KEYS = {
    "character": "characters",
    "costume": "costumes",
    "scene": "scenes",
    "prop": "props",
    "character_voice": "character_voices",
    "music": "music",
    "ambience": "ambience",
    "sound_effect": "sound_effects",
}
MAX_EPISODES_PER_WORK_UNIT = 2
MAX_SCRIPT_CHARS_PER_WORK_UNIT = 16_000


@dataclass(frozen=True)
class BreakdownWorkUnit:
    group: str
    requirement_types: tuple[str, ...]
    episodes: tuple[Any, ...]
    input_chars: int
    script_ranges: tuple[tuple[int, int, int], ...] = ()

    @property
    def episode_numbers(self) -> list[int]:
        return [int(episode.number) for episode in self.episodes]

    @property
    def source_ranges(self) -> list[dict[str, int]]:
        return [{"episode_number": number, "start": start, "end": end}
                for number, start, end in self.script_ranges]

    def episode_payload(self) -> list[dict[str, Any]]:
        ranges = {number: (start, end) for number, start, end in self.script_ranges}
        rows = []
        for episode in self.episodes:
            script = str(episode.script or "")
            row = {"number": episode.number, "title": episode.title, "script": script}
            if episode.number in ranges:
                start, end = ranges[episode.number]
                row.update(script=script[start:end], source_range={"start": start, "end": end},
                           preceding_context=script[max(0, start - 300):start],
                           following_context=script[end:end + 300])
            rows.append(row)
        return rows


def script_chunk_ranges(script: str) -> list[tuple[int, int]]:
    """Partition every character; prefer paragraph boundaries without discarding text."""
    ranges = []
    start = 0
    while start < len(script):
        end = min(start + MAX_SCRIPT_CHARS_PER_WORK_UNIT, len(script))
        if end < len(script):
            boundary = script.rfind("\n", start + MAX_SCRIPT_CHARS_PER_WORK_UNIT // 2, end)
            if boundary >= 0:
                end = boundary + 1
        ranges.append((start, end))
        start = end
    return ranges


def restore_work_unit(
    episodes: Iterable[Any], requirement_types: Iterable[str], group: str,
    source_ranges: list[dict[str, int]] | None = None,
) -> BreakdownWorkUnit:
    episodes = tuple(episodes)
    ranges = tuple((int(row["episode_number"]), int(row["start"]), int(row["end"]))
                   for row in source_ranges or [])
    lengths = {int(episode.number): _episode_characters(episode) for episode in episodes}
    if ranges and (
        len(ranges) != len(lengths) or {row[0] for row in ranges} != set(lengths)
        or any(not 0 <= start < end <= lengths[number] for number, start, end in ranges)
    ):
        raise ValueError("资产拆解原文片段范围无效")
    return BreakdownWorkUnit(
        group=group, requirement_types=tuple(requirement_types), episodes=episodes,
        input_chars=sum(end - start for _, start, end in ranges) if ranges else sum(lengths.values()),
        script_ranges=ranges,
    )


def smaller_work_units(unit: BreakdownWorkUnit) -> list[BreakdownWorkUnit]:
    """Narrow a truncated range without replaying successful source fragments."""
    if len(unit.episodes) > 1:
        return [restore_work_unit((episode,), unit.requirement_types, unit.group)
                for episode in unit.episodes]
    episode = unit.episodes[0]
    start, end = unit.script_ranges[0][1:] if unit.script_ranges else (0, len(episode.script or ""))
    if end - start < 2:
        return [unit]
    middle = start + (end - start) // 2
    boundary = str(episode.script).rfind("\n", start + (end - start) // 4, middle)
    if boundary >= 0:
        middle = boundary + 1
    return [restore_work_unit(unit.episodes, unit.requirement_types, unit.group,
                              [{"episode_number": episode.number, "start": a, "end": b}])
            for a, b in ((start, middle), (middle, end))]


def _episode_characters(episode: Any) -> int:
    return len(str(getattr(episode, "script", "") or ""))


def adaptive_episode_batches(episodes: Iterable[Any]) -> list[tuple[Any, ...]]:
    batches: list[tuple[Any, ...]] = []
    current: list[Any] = []
    current_chars = 0
    for episode in episodes:
        episode_chars = _episode_characters(episode)
        exceeds_budget = current and current_chars + episode_chars > MAX_SCRIPT_CHARS_PER_WORK_UNIT
        if current and (len(current) >= MAX_EPISODES_PER_WORK_UNIT or exceeds_budget):
            batches.append(tuple(current))
            current = []
            current_chars = 0
        current.append(episode)
        current_chars += episode_chars
    if current:
        batches.append(tuple(current))
    return batches


def build_breakdown_work_units(
    episodes: Iterable[Any], requirement_types: Iterable[str]
) -> tuple[list[BreakdownWorkUnit], list[BreakdownWorkUnit]]:
    selected = tuple(dict.fromkeys(requirement_types))
    batches = adaptive_episode_batches(episodes)
    visual_types = tuple(value for value in selected if value in VISUAL_REQUIREMENT_TYPES)
    audio_types = tuple(value for value in selected if value in AUDIO_REQUIREMENT_TYPES)

    def units(group: str, types: tuple[str, ...]) -> list[BreakdownWorkUnit]:
        if not types:
            return []
        result = []
        for batch in batches:
            input_chars = sum(_episode_characters(episode) for episode in batch)
            if len(batch) == 1 and input_chars > MAX_SCRIPT_CHARS_PER_WORK_UNIT:
                episode = batch[0]
                for start, end in script_chunk_ranges(str(episode.script or "")):
                    result.append(BreakdownWorkUnit(
                        group=group, requirement_types=types, episodes=batch,
                        input_chars=end - start,
                        script_ranges=((int(episode.number), start, end),),
                    ))
            else:
                result.append(BreakdownWorkUnit(
                    group=group, requirement_types=types, episodes=batch, input_chars=input_chars,
                ))
        return result

    return (
        units("visual", visual_types),
        units("audio", audio_types),
    )


def structured_output_parameters(
    *, protocol: str, capabilities: Iterable[str], max_output_tokens: int | None = None
) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    if max_output_tokens is not None:
        parameters["max_tokens"] = max_output_tokens
    capability_set = set(capabilities)
    if protocol == PROTOCOL_GOOGLE_GEMINI:
        parameters["responseMimeType"] = "application/json"
        if "json_schema" in capability_set:
            parameters["responseSchema"] = asset_breakdown_response_schema()
    elif "json_schema" in capability_set:
        parameters["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "script_asset_breakdown",
                "strict": False,
                "schema": asset_breakdown_response_schema(lowercase=True),
            },
        }
    return parameters


def asset_breakdown_response_schema(*, lowercase: bool = False) -> dict[str, Any]:
    object_type, array_type, string_type, integer_type, boolean_type = (
        ("object", "array", "string", "integer", "boolean")
        if lowercase
        else ("OBJECT", "ARRAY", "STRING", "INTEGER", "BOOLEAN")
    )
    usage = {
        "type": object_type,
        "properties": {
            "asset_key": {"type": string_type},
            "episode_number": {"type": integer_type},
            "scene": {"type": string_type},
            "source_excerpt": {"type": string_type},
            "performance": {"type": string_type},
            "timing": {"type": string_type},
            "needs_review": {"type": boolean_type},
        },
        "required": ["asset_key", "episode_number"],
    }
    for name in ("scene", "source_excerpt", "performance", "timing"):
        usage["properties"][name] = (
            {"type": ["string", "null"]}
            if lowercase else {"type": "STRING", "nullable": True}
        )
    asset = {
        "type": object_type,
        "properties": {
            "name": {"type": string_type},
            "aliases": {"type": array_type, "items": {"type": string_type}},
            "episode_numbers": {"type": array_type, "items": {"type": integer_type}},
            "description": {"type": string_type},
            "prompt_anchor": {"type": string_type},
            "attributes": {"type": object_type},
        },
        "required": [
            "name",
            "aliases",
            "episode_numbers",
            "description",
            "prompt_anchor",
            "attributes",
        ],
    }
    properties: dict[str, Any] = {"reply": {"type": string_type}}
    for result_key in REQUIREMENT_RESULT_KEYS.values():
        properties[result_key] = {"type": array_type, "items": asset}
    properties["usage_records"] = {"type": array_type, "items": usage}
    return {
        "type": object_type,
        "properties": properties,
        "required": ["reply", *REQUIREMENT_RESULT_KEYS.values(), "usage_records"],
    }


def normalized_asset_key(requirement_type: str, name: str) -> str:
    normalized_name = "".join(str(name).strip().casefold().split())
    return f"{requirement_type}:{normalized_name}"


def breakdown_scope_key(
    *,
    input_fingerprint: str,
    group: str,
    requirement_types: Iterable[str],
    episode_numbers: Iterable[int],
    provider_model_id: int,
    skill_key: str | None,
    skill_version: int | None,
    source_ranges: list[dict[str, int]] | None = None,
) -> str:
    """Build an immutable range identity used by resume and supersession logic."""
    value = {
        "input_fingerprint": input_fingerprint,
        "group": group,
        "requirement_types": sorted(set(requirement_types)),
        "episode_numbers": sorted(set(int(number) for number in episode_numbers)),
        "provider_model_id": int(provider_model_id),
        "skill_key": skill_key or "",
        "skill_version": int(skill_version or 0),
    }
    if source_ranges:
        value["source_ranges"] = source_ranges
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"asset-breakdown:{digest}"


def visual_asset_key_context(
    breakdowns: Iterable[dict[str, Any]],
    *,
    episode_numbers: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    requested_numbers = {int(value) for value in episode_numbers or []}
    rows: dict[str, dict[str, Any]] = {}
    for breakdown in breakdowns:
        for requirement_type in VISUAL_REQUIREMENT_TYPES:
            for item in breakdown.get(REQUIREMENT_RESULT_KEYS[requirement_type], []):
                item_numbers = {int(value) for value in item.get("episode_numbers") or []}
                if requested_numbers and item_numbers and not requested_numbers.intersection(
                    item_numbers
                ):
                    continue
                attributes = dict(item.get("attributes") or {})
                key = str(attributes.get("asset_key") or "").strip() or normalized_asset_key(
                    requirement_type, item.get("name", "")
                )
                rows[key] = {
                    "asset_key": key,
                    "requirement_type": requirement_type,
                    "name": item.get("name"),
                    "aliases": list(item.get("aliases") or []),
                    "episode_numbers": sorted(item_numbers),
                }
    return list(rows.values())
