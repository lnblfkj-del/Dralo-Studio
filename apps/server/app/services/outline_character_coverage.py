"""Deterministic Story Bible to episode-outline cast coverage checks."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.core.errors import ConflictError


def _name_index(story: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    aliases: dict[str, str] = {}
    rows: dict[str, dict[str, Any]] = {}
    for character in story.get("characters") or []:
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        rows[name] = character
        for candidate in [name, *(character.get("aliases") or [])]:
            normalized = str(candidate or "").strip().casefold()
            if normalized and normalized not in aliases:
                aliases[normalized] = name
    return aliases, rows


def canonicalize_episode_characters(story: dict[str, Any], names: list[Any]) -> list[str]:
    aliases, _ = _name_index(story)
    canonical: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for value in names:
        raw = str(value or "").strip()
        if not raw:
            continue
        name = aliases.get(raw.casefold())
        if name is None:
            unknown.append(raw)
            continue
        key = name.casefold()
        if key not in seen:
            canonical.append(name)
            seen.add(key)
    if unknown:
        raise ConflictError(f"分集角色不属于当前故事设定：{'、'.join(unknown)}")
    return canonical


def canonicalize_outline(story: dict[str, Any], outline: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(outline)
    for episode in normalized.get("episodes") or []:
        episode["characters"] = canonicalize_episode_characters(story, list(episode.get("characters") or []))
    return normalized


def _scope_range(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    match = re.search(r"(?:第\s*)?(\d{1,3})\s*(?:-|–|—|至|到)\s*(\d{1,3})\s*集?", text)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    from app.core.creation_limits import MAX_EPISODES
    return (start, end) if 1 <= start <= end <= MAX_EPISODES else None


def _longest_gap(numbers: list[int], episode_count: int) -> tuple[int, int, int]:
    points = [0, *sorted(set(numbers)), episode_count + 1]
    best = (0, 0, 0)
    for left, right in zip(points, points[1:]):
        gap = right - left - 1
        if gap > best[0]:
            best = (gap, left + 1, right - 1)
    return best


def review_outline_character_coverage(story: dict[str, Any], outline: dict[str, Any]) -> dict[str, Any]:
    _, characters = _name_index(story)
    episodes = list(outline.get("episodes") or [])
    episode_count = len(episodes)
    appearances = {name: [] for name in characters}
    warnings: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    for episode in episodes:
        number = int(episode.get("number") or 0)
        names = list(episode.get("characters") or [])
        if not names:
            warnings.append({"code": "episode_cast_missing", "episode_number": number, "message": f"第 {number} 集尚未规划登场角色。"})
        for name in names:
            if name in appearances:
                appearances[name].append(number)
            else:
                warnings.append({"code": "unknown_outline_character", "episode_number": number, "character": name, "message": f"第 {number} 集包含故事设定外角色“{name}”。"})
        episode_rows.append({"number": number, "characters": names})
    character_rows: list[dict[str, Any]] = []
    for name, character in characters.items():
        numbers = appearances[name]
        importance = str(character.get("importance") or "")
        row_warnings: list[str] = []
        if not numbers:
            code = "unused_story_character"
            message = f"故事角色“{name}”尚未安排到任何分集。"
            warnings.append({"code": code, "character": name, "message": message})
            row_warnings.append(code)
        if numbers and importance in {"core", "recurring"}:
            gap, start, end = _longest_gap(numbers, episode_count)
            limit = max(3, (episode_count + 7) // 8) if importance == "core" else max(5, (episode_count + 4) // 5)
            if gap > limit:
                code = f"{importance}_character_long_gap"
                message = f"{('核心' if importance == 'core' else '常驻')}角色“{name}”在第 {start}-{end} 集连续缺席 {gap} 集。"
                warnings.append({"code": code, "character": name, "episode_start": start, "episode_end": end, "message": message})
                row_warnings.append(code)
        if numbers and importance == "phase":
            scope = _scope_range(character.get("appearance_scope"))
            ranges = [(span["start"], span["end"]) for span in character.get("appearance_ranges", [])]
            if not ranges and scope:
                ranges = [scope]
            if not ranges:
                warnings.append({"code": "phase_scope_unverifiable", "character": name, "message": f"阶段角色“{name}”的出场范围无法机器校验，请使用如“第21-35集”的明确范围。"})
                row_warnings.append("phase_scope_unverifiable")
            else:
                outside = [number for number in numbers if not any(start <= number <= end for start, end in ranges)]
                if outside:
                    label = "、".join(f"{start}-{end}" for start, end in ranges)
                    warnings.append({"code": "phase_scope_mismatch", "character": name, "episode_numbers": outside, "message": f"阶段角色“{name}”在设定范围 {label} 集之外出现：{','.join(map(str, outside))}。"})
                    row_warnings.append("phase_scope_mismatch")
        character_rows.append({
            "name": name,
            "importance": importance or None,
            "appearance_scope": character.get("appearance_scope"),
            "planned_episodes": numbers,
            "planned_count": len(numbers),
            "warnings": row_warnings,
        })
    return {"episode_count": episode_count, "character_rows": character_rows, "episode_rows": episode_rows, "warnings": warnings}


def coverage_scope(request: dict[str, Any], sources: dict[str, dict[str, Any]], story: dict[str, Any], outline: dict[str, Any]) -> dict[str, Any]:
    story_source = sources.get("story_bible") or {}
    outline_source = sources.get("episode_outline") or {}
    if request.get("story_artifact_id") != story_source.get("id") or request.get("story_expected_revision") != story_source.get("revision"):
        raise ConflictError("故事设定已更新，请刷新后重新生成角色覆盖提案")
    if request.get("outline_artifact_id") != outline_source.get("id") or request.get("outline_expected_revision") != outline_source.get("revision"):
        raise ConflictError("分集大纲已更新，请刷新后重新生成角色覆盖提案")
    return {**request, "story_snapshot": deepcopy(story), "outline_snapshot": deepcopy(outline)}


def restrict_outline_coverage(scope: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    story = dict(scope.get("story_snapshot") or {})
    baseline = dict(scope.get("outline_snapshot") or {})
    proposed_rows = list((proposed or {}).get("episodes") or [])
    baseline_rows = list(baseline.get("episodes") or [])
    if len(proposed_rows) != len(baseline_rows):
        raise ConflictError("角色覆盖提案改变了分集数量")
    restricted: list[dict[str, Any]] = []
    for before, after in zip(baseline_rows, proposed_rows):
        if int(after.get("number") or 0) != int(before.get("number") or 0):
            raise ConflictError("角色覆盖提案改变了分集顺序")
        restricted.append({**deepcopy(before), "characters": canonicalize_episode_characters(story, list(after.get("characters") or []))})
    return {**deepcopy(baseline), "episodes": restricted}
