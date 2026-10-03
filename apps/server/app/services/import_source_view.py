"""Bounded source views and local boundary previews; never mutate the import."""

import re

from app.schemas.script_import import ScriptImportSessionOut

_VALUE = r"\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:[-–—~至]\s*(\d+(?:\.\d+)?))?\s*(秒|分钟|seconds?|secs?|s\b|minutes?|mins?)"
_OWN = re.compile(r"(?:^|\n)\s*(?:总时长|时长|每集时长|单集时长|建议单集时长|total\s+(?:duration|runtime|running time)|duration|runtime|running time)" + _VALUE, re.I)
_COMMON = re.compile(r"(?:^|\n)\s*(?:每集时长|单集时长|建议单集时长|duration per episode|episode duration)" + _VALUE, re.I)


def duration_hint(text, preface):
    own = _OWN.search(text)
    match = own or _COMMON.search(preface)
    if not match:
        return {"label": "待填写", "detail": "未找到明确时长标注", "suggested": None}
    factor = 60 if re.search(r"分钟|minute|min", match[3], re.I) else 1
    low, high = float(match[1]) * factor, float(match[2] or match[1]) * factor
    if low < 1 or high < low or high > 3600:
        return {"label": "待填写", "detail": "原文时长超出支持范围", "suggested": None}
    span = f"{low:g}—{high:g}" if match[2] else f"{low:g}"
    return {"label": "原文范围" if match[2] else "原文标注", "detail": f"{'本集' if own else '全剧通用'} {span} 秒", "suggested": int((low + high) / 2 + 0.5)}


def session_view(item, compact=False):
    result = ScriptImportSessionOut.model_validate(item)
    result.source_char_count = len(item.source_text)
    if compact:
        result.source_text = ""
        preface = item.source_text[:item.episode_boundaries[0]["start"]] if item.episode_boundaries else ""
        result.duration_hints = {
            str(row["start"]): duration_hint(item.source_text[row["start"]:row["end"]], preface)
            for row in item.episode_boundaries
        }
    return result


def split_point(source, start, end):
    middle = (start + end) // 2
    before = source.rfind("\n", start, middle + 1)
    after = source.find("\n", middle, end)
    candidates = [p + 1 for p in (before, after) if p >= 0 and start < p + 1 < end]
    point = min(candidates, key=lambda p: abs(p - middle)) if candidates else middle
    return {"point": point, "first_count": len(source[start:point].strip()), "second_count": len(source[point:end].strip())}
