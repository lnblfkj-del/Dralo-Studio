"""N4 分集大纲结构合同与质量审阅。

硬规则保护可执行的数据合同；软规则只形成审阅报告，避免偶发模型重复
阻断用户继续编辑。该服务不负责写库，调用方决定是否持久化报告。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import ConflictError
from app.services.narrative_spec_service import narrative_spec_from_settings


_PREVIOUS_EPISODE_TERMS = (
    "上一集", "上集", "承接上集", "承接上一集", "接上集", "接上一集", "接上回",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _episode_text(row: dict[str, Any]) -> str:
    return "\n".join(
        str(row.get(field) or "")
        for field in ("synopsis", "dramatic_goal", "cliffhanger")
    )


def _has_previous_episode_handoff(row: dict[str, Any]) -> bool:
    text = _episode_text(row)
    return any(term in text for term in _PREVIOUS_EPISODE_TERMS)


def _unit_for_number(spec: dict[str, Any], number: int) -> dict[str, Any] | None:
    matches = [
        unit
        for unit in spec.get("units") or []
        if int(unit.get("episode_start") or 0) <= number <= int(unit.get("episode_end") or 0)
    ]
    if len(matches) > 1:
        raise ConflictError(f"第 {number} 集同时落入多个故事单元")
    return matches[0] if matches else None


def validate_outline_structure(
    content: dict[str, Any],
    spec: dict[str, Any],
    *,
    story: dict[str, Any] | None = None,
) -> None:
    """Raise only for deterministic contract violations."""

    episodes = list(content.get("episodes") or [])
    expected = int(spec.get("episode_count") or 0)
    if spec.get("status") == "confirmed" and expected and len(episodes) != expected:
        raise ConflictError(f"分集大纲必须为 {expected} 集，当前为 {len(episodes)} 集")
    numbers = [int(row.get("number") or 0) for row in episodes]
    if numbers != list(range(1, len(episodes) + 1)):
        raise ConflictError("分集编号必须从 1 开始连续排列")

    if spec.get("structure") == "unit":
        units = list(spec.get("units") or [])
        if not units:
            raise ConflictError("已确认单元故事但没有可用的单元范围")
        known_ids = {str(unit.get("unit_id")) for unit in units}
        for row in episodes:
            number = int(row["number"])
            unit = _unit_for_number(spec, number)
            if unit is None:
                raise ConflictError(f"第 {number} 集不属于任何已确认故事单元")
            declared = str(row.get("unit_id") or "").strip()
            if declared and declared not in known_ids:
                raise ConflictError(f"第 {number} 集使用了不存在的故事单元 {declared}")
            if declared and declared != str(unit.get("unit_id")):
                raise ConflictError(f"第 {number} 集跨越了错误的故事单元")

    if story is not None:
        from app.services.outline_character_coverage import canonicalize_outline

        canonicalize_outline(story, content)


def review_outline_structure(
    content: dict[str, Any],
    spec: dict[str, Any],
    *,
    story: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable, UI-ready review report without changing user content."""

    episodes = list(content.get("episodes") or [])
    warnings: list[dict[str, Any]] = []
    seen_titles: dict[str, int] = {}
    seen_synopses: dict[str, int] = {}
    unit_rows: list[dict[str, Any]] = []
    for row in episodes:
        number = int(row.get("number") or 0)
        title = _clean(row.get("title"))
        synopsis = _clean(row.get("synopsis"))
        if title:
            if title in seen_titles:
                warnings.append({
                    "code": "duplicate_episode_title",
                    "episode_numbers": [seen_titles[title], number],
                    "message": f"第 {seen_titles[title]} 集与第 {number} 集标题重复。",
                })
            else:
                seen_titles[title] = number
        if synopsis and len(synopsis) >= 18:
            if synopsis in seen_synopses:
                warnings.append({
                    "code": "duplicate_episode_synopsis",
                    "episode_numbers": [seen_synopses[synopsis], number],
                    "message": f"第 {seen_synopses[synopsis]} 集与第 {number} 集梗概重复。",
                })
            else:
                seen_synopses[synopsis] = number

        if spec.get("structure") in {"independent", "hybrid"} and not str(row.get("cliffhanger") or "").strip():
            warnings.append({
                "code": "episode_ending_not_described",
                "episode_number": number,
                "message": f"第 {number} 集未填写结尾语义，请确认本集是否已经收束。",
            })
        if (
            spec.get("structure") in {"independent", "hybrid"}
            and number > 1
            and _has_previous_episode_handoff(row)
        ):
            warnings.append({
                "code": "previous_episode_handoff_not_allowed",
                "episode_number": number,
                "message": f"第 {number} 集出现上一集承接语义，请确认是否错误继承了临时剧情。",
            })
        if spec.get("structure") == "continuous" and not str(row.get("cliffhanger") or "").strip():
            warnings.append({
                "code": "continuous_handoff_missing",
                "episode_number": number,
                "message": f"第 {number} 集未填写集尾交接，请确认连续主线是否有明确推进。",
            })
        if spec.get("structure") == "unit":
            unit = _unit_for_number(spec, number)
            unit_rows.append({
                "episode_number": number,
                "unit_id": unit.get("unit_id") if unit else None,
                "declared_unit_id": row.get("unit_id"),
            })
            if (
                unit is not None
                and number == int(unit.get("episode_start") or 0)
                and number > 1
                and _has_previous_episode_handoff(row)
            ):
                warnings.append({
                    "code": "unit_boundary_previous_handoff",
                    "episode_number": number,
                    "message": f"第 {number} 集为单元首集，却出现上一集承接语义。",
                })

    if spec.get("structure") in {"continuous", "hybrid"}:
        for left, right in zip(episodes, episodes[1:]):
            if _clean(left.get("synopsis")) and _clean(left.get("synopsis")) == _clean(right.get("synopsis")):
                warnings.append({
                    "code": "consecutive_event_repeat",
                    "episode_numbers": [left.get("number"), right.get("number")],
                    "message": f"第 {left.get('number')}—{right.get('number')} 集可能重复同一事件。",
                })

    report: dict[str, Any] = {
        "structure": spec.get("structure"),
        "episode_count": len(episodes),
        "expected_episode_count": spec.get("episode_count"),
        "warnings": warnings,
        "warning_count": len(warnings),
    }
    if unit_rows:
        report["units"] = unit_rows
    if story is not None:
        from app.services.outline_character_coverage import review_outline_character_coverage

        report["character_coverage"] = review_outline_character_coverage(story, content)
    return report


def review_for_settings(
    content: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    story: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = narrative_spec_from_settings(settings)
    validate_outline_structure(content, spec, story=story)
    # 校验阶段会解析角色别名，但审阅报告保留用户填写的原始名称，
    # 这样前端可以准确指出用户实际输入的角色名。
    return review_outline_structure(content, spec, story=story)
