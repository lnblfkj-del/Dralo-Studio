"""Strict JSON extraction and schema validation for model text responses."""

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.core.errors import (
    ModelOutputPollutedError,
    ModelOutputSchemaError,
    ModelOutputTruncatedError,
)

_FULL_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?\s*(.*?)\s*```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)
_WRAPPER_KEYS = {"result", "data", "output", "response", "content"}


def _edge_character(value: str, *, first: bool) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[0] if first else stripped[-1]


def _diagnostic(value: str, **extra: Any) -> dict[str, Any]:
    return {
        "input_chars": len(value),
        "first_non_whitespace": _edge_character(value, first=True),
        "last_non_whitespace": _edge_character(value, first=False),
        **extra,
    }


def _scan_top_level(value: str, start: int) -> tuple[int | None, str | None]:
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index in range(start, len(value)):
        character = value[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or stack[-1] != pairs[character]:
                return None, "mismatched_closer"
            stack.pop()
            if not stack:
                return index + 1, None
    if in_string:
        return None, "unclosed_string"
    if stack:
        return None, "unclosed_container"
    return None, "missing_top_level"


def parse_json_document(text: str, label: str) -> tuple[Any, dict[str, Any]]:
    """Parse exactly one top-level JSON object/array without fragment recovery."""
    original = str(text or "")
    value = original.strip()
    fenced = _FULL_JSON_FENCE.fullmatch(value)
    if fenced:
        value = fenced.group(1).strip()
    opener_positions = [position for token in ("{", "[") if (position := value.find(token)) >= 0]
    if not opener_positions:
        raise ModelOutputSchemaError(
            f"模型未返回有效的{label} JSON，请重新生成",
            details=_diagnostic(original, top_level_closed=False, fenced=bool(fenced)),
        )
    start = min(opener_positions)
    end, scan_error = _scan_top_level(value, start)
    if scan_error == "mismatched_closer":
        raise ModelOutputSchemaError(
            f"模型返回的{label}括号结构无效，请使用已保存响应重新处理或重新生成",
            details=_diagnostic(
                original,
                top_level_closed=False,
                scan_error=scan_error,
                prefix_chars=len(value[:start].strip()),
                fenced=bool(fenced),
            ),
        )
    if end is None:
        raise ModelOutputTruncatedError(
            f"模型返回的{label} JSON 未完整结束，请使用已保存响应重新处理或重新生成",
            details=_diagnostic(
                original,
                top_level_closed=False,
                scan_error=scan_error,
                prefix_chars=len(value[:start].strip()),
                fenced=bool(fenced),
            ),
        )
    prefix = value[:start].strip()
    suffix = value[end:].strip()
    if prefix or suffix:
        raise ModelOutputPollutedError(
            f"模型返回的{label}包含 JSON 之外的前缀或尾随内容，请使用已保存响应重新处理或重新生成",
            details=_diagnostic(
                original,
                top_level_closed=True,
                prefix_chars=len(prefix),
                trailing_chars=len(suffix),
                fenced=bool(fenced),
            ),
        )
    document = value[start:end]
    try:
        decoded = json.loads(document)
    except json.JSONDecodeError as exc:
        raise ModelOutputSchemaError(
            f"模型返回的{label}不是合法 JSON，请使用已保存响应重新处理或重新生成",
            details=_diagnostic(
                original,
                top_level_closed=True,
                json_error_position=exc.pos,
                json_error_line=exc.lineno,
                json_error_column=exc.colno,
                fenced=bool(fenced),
            ),
        ) from exc
    return decoded, _diagnostic(
        original,
        top_level_closed=True,
        prefix_chars=0,
        trailing_chars=0,
        fenced=bool(fenced),
    )


def _schema_candidates(decoded: Any) -> list[Any]:
    candidates: list[Any] = []

    def visit(candidate: Any) -> None:
        candidates.append(candidate)
        if isinstance(candidate, dict):
            for key, child in candidate.items():
                if key in _WRAPPER_KEYS and isinstance(child, (dict, list)):
                    visit(child)

    visit(decoded)
    return candidates


def recover_unique_structured_result(text, schema, label, *, repair=None, accepts=None):
    """Recover whole documents only; never join fragments or choose among valid drafts."""
    position = 0
    matches = {}
    documents = 0
    while position < len(text):
        starts = [p for token in ("{", "[") if (p := text.find(token, position)) >= 0]
        if not starts:
            break
        start = min(starts)
        end, error = _scan_top_level(text, start)
        if error or end is None:
            raise ModelOutputPollutedError(f"{label}含不完整或损坏的候选内容，不能安全恢复；原始响应已保留")
        documents += 1
        if documents > 16:
            raise ModelOutputPollutedError(f"{label}候选结果过多，不能安全恢复；原始响应已保留")
        try:
            candidate = parse_structured_result(text[start:end], schema, label, repair=repair)
        except (ModelOutputSchemaError, ModelOutputTruncatedError, ModelOutputPollutedError):
            candidate = None
        if candidate is not None and (accepts is None or accepts(candidate)):
            matches[json.dumps(candidate, sort_keys=True, ensure_ascii=False)] = candidate
        position = end
    if len(matches) != 1:
        raise ModelOutputPollutedError(
            f"{label}含 {len(matches)} 份符合要求的不同结果，无法唯一确定正文；原始响应已保留，请重新生成"
        )
    return next(iter(matches.values()))


def parse_structured_result(
    text: str,
    schema: Any,
    label: str,
    *,
    repair: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    decoded, diagnostic = parse_json_document(text, label)
    validation_error: ValidationError | None = None
    for raw_candidate in reversed(_schema_candidates(decoded)):
        candidate = repair(raw_candidate) if repair is not None else raw_candidate
        try:
            return schema.model_validate(candidate).model_dump()
        except ValidationError as exc:
            if validation_error is None:
                validation_error = exc
    assert validation_error is not None
    invalid_fields: list[str] = []
    field_errors: list[dict[str, str]] = []
    labels = {"characters": "角色", "age": "年龄", "name": "姓名", "title": "标题", "logline": "梗概", "event_timeline": "事件脉络", "episodes": "分集", "story_bible": "故事设定", "story_options": "故事方案", "synopsis": "梗概", "themes": "主题"}
    reasons = {"missing": "缺少必填字段", "string_type": "应为文本", "int_type": "应为整数", "int_parsing": "无法转换为整数", "list_type": "应为列表", "dict_type": "应为对象", "model_type": "应为完整对象", "literal_error": "不在允许的选项内", "string_too_long": "文本超过字段长度限制", "string_too_short": "文本未达到字段最小长度", "greater_than_equal": "数值低于允许下限", "less_than_equal": "数值超过允许上限"}
    inputs = validation_error.errors(include_url=False, include_input=True)
    for error in inputs:
        field = ".".join(str(part) for part in error.get("loc", ())) or "根对象"
        if field not in invalid_fields:
            invalid_fields.append(field)
            reason = reasons.get(error["type"], "未满足字段校验规则")
            context = error.get("ctx") or {}
            for key, label_text in (("max_length", "最大长度"), ("min_length", "最小长度"), ("ge", "最小值"), ("le", "最大值")):
                if isinstance(context.get(key), (int, float)):
                    reason += f"（{label_text}：{context[key]}）"
            field_errors.append({
                "field": field,
                "label": " / ".join(f"第 {part + 1} 项" if isinstance(part, int) else labels.get(part, str(part)) for part in error.get("loc", ())) or "根对象",
                "reason": reason,
                "expected": error["type"],
                "actual_type": "missing" if error["type"] == "missing" else type(error.get("input")).__name__,
            })
    detail = "、".join(invalid_fields[:6])
    raise ModelOutputSchemaError(
        f"模型返回的{label}结构不完整或字段无效（字段：{detail}）；原始响应已保留，请核对字段要求后重新生成",
        details={**diagnostic, "invalid_fields": invalid_fields[:12], "field_errors": field_errors[:12]},
    ) from validation_error
