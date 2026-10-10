"""Deterministic, warning-first structure review for generated screenplays."""

from typing import Any

from app.services.screenplay_review import (
    canonical_format_warnings, character_catalog, duration_estimate, review_sources, spoken_char_count,
)

_DIRECT_HANDOFF_TERMS = ("上一集", "上集", "承接上集", "接上集", "前集")
_OPEN_ENDING_TERMS = ("未完待续", "下集继续", "下集揭晓", "欲知后事")
_GENERIC_HANDOFF_STATES = {
    "承接上一集",
    "承接上集",
    "接上一集",
    "接上集",
    "延续上一集",
    "延续上集",
}


def build_structure_review_context(
    generation_context: dict[str, Any],
    preceding_update: dict[str, Any] | None,
    outline_entry: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the compact source data used by post-generation review."""

    unit = dict(generation_context.get("unit") or {})
    return {
        "previous_continuity_update": dict(preceding_update or {}),
        "current_outline_cliffhanger": str(outline_entry.get("cliffhanger") or "").strip(),
        "unit_id": unit.get("unit_id"),
        "unit_episode_start": unit.get("episode_start"),
        "unit_episode_end": unit.get("episode_end"),
        "unit_persistent_facts": list(unit.get("persistent_facts") or []),
    }


def _text(value: object) -> str:
    return str(value or "").strip()


def _contains_any(value: object, terms: tuple[str, ...]) -> bool:
    text = _text(value)
    return any(term in text for term in terms)


def _issue(code: str, message: str, evidence: str = "") -> dict[str, str]:
    return {
        "code": code,
        "severity": "warning",
        "message": message,
        "evidence": evidence[:500],
    }


def _review_pacing(
    script: str, parameters: dict[str, Any], issues: list[dict[str, str]]
) -> dict[str, Any]:
    budget = dict(parameters.get("script_length_budget") or {})
    characters = parameters.get("screenplay_characters")
    if characters is None:
        characters = [{"name": name} for name in parameters.get("known_character_names") or []]
    review = review_sources(script, character_catalog(characters))
    dialogue_chars = review["dialogue_chars"]
    counted_lines = review["dialogue_events"]
    action_lines = sum(record["kind"] == "action" for record in review["records"])
    for record in review["records"]:
        if record["kind"] != "dialogue":
            continue
        count = spoken_char_count(record["spoken_text"])
        if count > 30:
            issues.append(_issue(
                "long_spoken_line",
                f"第 {record['line']} 行 {record['speaker']} 的台词约 {count} 字，建议拆句或留动作反应。",
                record["text"],
            ))

    for error in review["errors"]:
        issues.append(_issue("screenplay_source_unresolved", error["message"]))
    if review["unbound_speakers"]:
        issues.append(_issue("screenplay_speaker_unbound", "带引号对白已保留，制作前仍需关联发声主体。",
                             "、".join(review["unbound_speakers"])))
    if parameters.get("screenplay_format_version"):
        drift = canonical_format_warnings(script)
        if drift:
            issues.append(_issue("screenplay_format_drift", "部分正文未采用统一行式模板；可识别内容保留，不自动改写。",
                                 "行号：" + "、".join(map(str, drift))))

    if counted_lines >= 2 and action_lines == 0:
        issues.append(_issue(
            "visible_action_missing",
            "已识别多句对白，但未发现独立的可见行动或反应段落；请人工核对画面推进。",
        ))

    maximum = int(budget.get("maximum_chars") or 0)
    if maximum and len(script) > round(maximum * 1.25):
        issues.append(_issue(
            "screenplay_above_target",
            f"正文约 {len(script)} 字，超过目标篇幅建议；请核对实际表演时长，不会截断或阻止保存。",
        ))
    spoken_target = int(budget.get("dialogue_chars") or 0)
    if spoken_target and dialogue_chars > round(spoken_target * 1.25):
        issues.append(_issue(
            "dialogue_above_target",
            f"已识别对白约 {dialogue_chars} 字，建议核对停顿、动作与成片时长。",
        ))
    return {
        "policy_version": parameters.get("screenplay_pacing_policy_version"),
        "script_chars": len(script),
        "recognized_dialogue_chars": dialogue_chars,
        "recognized_dialogue_lines": counted_lines,
        "action_lines": action_lines,
        "dialogue_coverage": "complete" if not review["errors"] else "partial",
        "source_review": {key: value for key, value in review.items() if key != "records"},
        "duration_estimate": duration_estimate(review),
        "speaker_kind_counts": {kind: sum(row.get("speaker_kind") == kind for row in review["records"])
                                for kind in ("character", "group", "narration", "device", "unbound")},
    }


def _review_closed_episode(
    continuity: dict[str, Any], script: str, issues: list[dict[str, str]]
) -> None:
    new_hooks = [_text(item) for item in continuity.get("new_hooks") or [] if _text(item)]
    ending = f"{_text(continuity.get('end_state'))}\n{script[-500:]}"
    # Metadata-only hooks are not evidence that this episode's core conflict is open.
    # Only explicit deferral is flagged; semantic closure still needs human review.
    deferred_hooks = [hook for hook in new_hooks if _contains_any(hook, _OPEN_ENDING_TERMS) or "下集" in hook]
    if deferred_hooks:
        issues.append(_issue(
            "episode_closure_new_hooks",
            "本集要求独立闭环，但连续性记录明确把问题留待下集，请核对本集核心冲突。",
            "；".join(deferred_hooks[:3]),
        ))
    if _contains_any(ending, _OPEN_ENDING_TERMS) or "下集" in ending:
        issues.append(_issue(
            "episode_closure_open_ending",
            "本集要求独立闭环，但结尾包含明确的待续或下集引导。",
            ending[-300:],
        ))


def _review_handoff(
    continuity: dict[str, Any], context: dict[str, Any], issues: list[dict[str, str]]
) -> None:
    previous = dict(context.get("previous_continuity_update") or {})
    if not previous:
        issues.append(_issue(
            "handoff_source_missing",
            "本集要求承接上一集，但任务未冻结上一集连续性快照，请人工核对开场交接。",
        ))
        return
    start_state = _text(continuity.get("start_state"))
    previous_end = _text(previous.get("end_state"))
    if previous_end and (
        start_state in _GENERIC_HANDOFF_STATES
        or (len(start_state) <= 12 and _contains_any(start_state, _DIRECT_HANDOFF_TERMS))
    ):
        issues.append(_issue(
            "handoff_detail_missing",
            "本集开场只写了泛化承接语，未体现已冻结的上一集结束状态。",
            f"上一集结束：{previous_end}；本集开始：{start_state}",
        ))
    previous_hooks = [_text(item) for item in previous.get("new_hooks") or [] if _text(item)]
    if previous_hooks:
        current_text = "\n".join([
            start_state,
            _text(continuity.get("end_state")),
            *[_text(item) for item in continuity.get("resolved_hooks") or []],
            *[_text(item) for item in continuity.get("new_hooks") or []],
        ])
        if not any(hook in current_text for hook in previous_hooks):
            issues.append(_issue(
                "handoff_hook_unreferenced",
                "上一集新增悬念未在本集连续性记录中直接体现，请人工核对是否遗漏承接。",
                "；".join(previous_hooks[:3]),
            ))


def review_episode_script_structure(
    generated: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Review structural compliance without rejecting or regenerating paid output."""

    continuity = dict(generated.get("continuity_update") or {})
    context = dict(parameters.get("structure_review_context") or {})
    structure = _text(parameters.get("narrative_structure")) or "continuous"
    strategy = _text(parameters.get("context_strategy")) or "continuous_series"
    requires_previous = bool(parameters.get("requires_previous_episode"))
    episode_closed = bool(parameters.get("episode_closed"))
    script = _text(generated.get("script"))
    start_state = _text(continuity.get("start_state"))
    end_state = _text(continuity.get("end_state"))
    issues: list[dict[str, str]] = []

    if not start_state:
        issues.append(_issue("start_state_missing", "连续性记录缺少本集开始状态。"))
    if not end_state:
        issues.append(_issue("end_state_missing", "连续性记录缺少本集结束状态。"))

    direct_handoff = _contains_any(start_state, _DIRECT_HANDOFF_TERMS)
    unit = dict(parameters.get("narrative_unit") or {})
    unit_start = int(context.get("unit_episode_start") or unit.get("episode_start") or 0)
    unit_end = int(context.get("unit_episode_end") or unit.get("episode_end") or 0)
    episode_number = int(generated.get("episode_number") or 0)
    dependencies = [
        int(item.get("episode_number") or 0)
        for item in parameters.get("dependency_episode_revisions") or []
        if isinstance(item, dict)
    ]

    if structure == "independent" and direct_handoff:
        issues.append(_issue(
            "independent_previous_handoff",
            "单集独立结构不应把上一集作为本集开场的必要前提。",
            start_state,
        ))
    elif structure == "hybrid" and direct_handoff:
        issues.append(_issue(
            "hybrid_temporary_handoff",
            "混合结构可以延续长期主线，但不应强制承接上一集临时事件。",
            start_state,
        ))
    elif structure == "unit" and episode_number == unit_start and direct_handoff:
        issues.append(_issue(
            "unit_boundary_handoff",
            "单元首集不应直接承接上一集或上一单元的临时事件。",
            start_state,
        ))

    if structure == "unit" and unit_start and unit_end:
        outside = [number for number in dependencies if number < unit_start or number > unit_end]
        if outside:
            issues.append(_issue(
                "unit_dependency_outside_range",
                "本集冻结的正文依赖超出当前单元范围。",
                "、".join(f"第 {number} 集" for number in outside),
            ))

    if requires_previous:
        _review_handoff(continuity, context, issues)
    if episode_closed and structure in {"independent", "unit"}:
        _review_closed_episode(continuity, script, issues)

    pacing = _review_pacing(script, parameters, issues)

    return {
        "version": 2,
        "status": "warning" if issues else "passed",
        "structure": structure,
        "context_strategy": strategy,
        "requires_previous_episode": requires_previous,
        "episode_closed": episode_closed,
        "unit_id": context.get("unit_id") or unit.get("unit_id"),
        "outline_cliffhanger": _text(context.get("current_outline_cliffhanger")),
        "pacing": pacing,
        "closure_review": {"scope": "explicit_deferral_only", "semantic_closure": "not_verified",
                           "reported_new_hooks": list(continuity.get("new_hooks") or [])},
        "issue_count": len(issues),
        "issues": issues,
    }


def structure_review_reason(report: dict[str, Any]) -> str | None:
    """Build a bounded episode status reason from a review report."""

    issues = list(report.get("issues") or [])
    if not issues:
        return None
    messages = [_text(item.get("message")) for item in issues[:3] if isinstance(item, dict)]
    suffix = f"；另有 {len(issues) - 3} 项" if len(issues) > 3 else ""
    return "正文结构检查提示：" + "；".join(messages) + suffix
