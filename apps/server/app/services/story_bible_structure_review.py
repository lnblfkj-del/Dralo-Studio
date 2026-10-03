"""Deterministic structure checks for Story Bible event planning."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.errors import ConflictError
from app.services.narrative_spec_service import narrative_spec_from_settings


_PREVIOUS_EPISODE_TERMS = (
    "上一集",
    "上集之后",
    "承接上集",
    "承接上一集",
    "接上集",
    "接上一集",
    "接上回",
)


def story_bible_structure_spec(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a structure that is authoritative for Story Bible drafting.

    A manual structure selection is an explicit user instruction even before the
    Story Bible itself is confirmed. Automatic guesses remain non-authoritative.
    """

    spec = narrative_spec_from_settings(settings)
    if not spec.get("structure"):
        return None
    if spec.get("status") == "confirmed" or spec.get("source") == "manual":
        return spec
    return None


def review_story_bible_structure(
    content: dict[str, Any], settings: dict[str, Any] | None
) -> dict[str, Any]:
    spec = story_bible_structure_spec(settings)
    events = [row for row in content.get("event_timeline") or [] if isinstance(row, dict)]
    if spec is None:
        return {
            "applicable": False,
            "structure": None,
            "event_count": len(events),
            "errors": [],
            "error_count": 0,
        }

    structure = str(spec["structure"])
    expected = int(spec.get("episode_count") or 0)
    errors: list[dict[str, Any]] = []
    hints = [row.get("episode_hint") for row in events]

    valid_hints = [value for value in hints if isinstance(value, int)]
    out_of_range = sorted({number for number in valid_hints if not 1 <= number <= expected})
    if out_of_range:
        errors.append({
            "code": "episode_hint_out_of_range",
            "episode_numbers": out_of_range,
            "message": f"事件集号超出 1-{expected}：{_format_numbers(out_of_range)}。",
        })

    if structure in {"independent", "hybrid"}:
        if len(events) != expected:
            errors.append({
                "code": "event_count_mismatch",
                "message": f"{expected} 集{_structure_name(structure)}必须提供 {expected} 个逐集事件，当前只有 {len(events)} 个。",
            })
        counts = Counter(valid_hints)
        missing = [number for number in range(1, expected + 1) if counts[number] == 0]
        duplicates = sorted(number for number, count in counts.items() if count > 1)
        if len(valid_hints) != len(events):
            errors.append({
                "code": "episode_hint_required",
                "message": "每个逐集事件都必须填写唯一的 episode_hint。",
            })
        if missing:
            errors.append({
                "code": "episode_hint_missing",
                "episode_numbers": missing,
                "message": f"事件规划缺少第 {_format_numbers(missing)} 集。",
            })
        if duplicates:
            errors.append({
                "code": "episode_hint_duplicate",
                "episode_numbers": duplicates,
                "message": f"第 {_format_numbers(duplicates)} 集被多个事件重复占用。",
            })

    if structure == "continuous" and not events:
        errors.append({
            "code": "continuous_event_timeline_required",
            "message": "连续故事必须保留能够建立全剧因果链的事件脉络。",
        })

    if structure == "unit":
        units = spec.get("units") or []
        if not events:
            errors.append({
                "code": "unit_event_timeline_required",
                "message": "单元故事必须为每个单元保留事件框架。",
            })
        for unit in units:
            start = int(unit.get("episode_start") or 0)
            end = int(unit.get("episode_end") or 0)
            if not any(isinstance(hint, int) and start <= hint <= end for hint in hints):
                errors.append({
                    "code": "unit_event_missing",
                    "unit_id": unit.get("unit_id"),
                    "message": f"单元“{unit.get('title') or unit.get('unit_id')}”第 {start}-{end} 集缺少归属事件。",
                })

    if structure == "independent":
        dependent = []
        for row in events:
            text = f"{row.get('title') or ''}\n{row.get('summary') or ''}"
            if any(term in text for term in _PREVIOUS_EPISODE_TERMS):
                dependent.append(row.get("episode_hint"))
        if dependent:
            errors.append({
                "code": "previous_episode_dependency",
                "episode_numbers": dependent,
                "message": "单集独立事件出现了上一集承接语义，必须改为当集可独立理解并闭环。",
            })

    return {
        "applicable": True,
        "structure": structure,
        "expected_episode_count": expected,
        "event_count": len(events),
        "episode_hints": hints,
        "errors": errors,
        "error_count": len(errors),
    }


def validate_story_bible_structure(
    content: dict[str, Any], settings: dict[str, Any] | None
) -> dict[str, Any]:
    report = review_story_bible_structure(content, settings)
    if report["errors"]:
        first = report["errors"][0]
        raise ConflictError(
            f"故事设定结构不合格：{first['message']} 请修改事件脉络或重新生成；系统不会自动再次调用模型。",
            details={"structure_review": report},
        )
    return report


def _structure_name(structure: str) -> str:
    return "单集独立结构" if structure == "independent" else "独立集＋长期主线结构"


def _format_numbers(numbers: list[int]) -> str:
    if len(numbers) <= 12:
        return "、".join(str(number) for number in numbers)
    head = "、".join(str(number) for number in numbers[:12])
    return f"{head} 等 {len(numbers)}"
