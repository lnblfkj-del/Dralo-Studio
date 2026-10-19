"""Conservative composition of verified, explicitly different appearance dimensions."""

# ruff: noqa: RUF001

import re

_DIMENSIONS = {
    "eyes": r"眼|\beyes?\b",
    "ears": r"耳|\bears?\b",
    "beard": r"胡须|胡子|\bbeard\b|\bmoustache\b|\bmustache\b",
    "hair": r"头发|发型|刘海|\bhair\b",
    "body": r"身材|体型|肚腩|胖|瘦|\bbuild\b|\bbell[yi]\b",
    "body_color": r"毛发|毛色|鳞片|鳞甲|外壳|甲壳|皮肤|\b(?:fur|scales?|shell|chassis|skin)\b",
    "tail": r"尾巴|尾部|\btail\b",
    "forehead_mark": r"额(?:间|头).*(?:印记|标记)|\bforehead\b.*\b(?:mark|marking)\b",
    "locomotion": r"直立行走|双足行走|四足行走|匍匐爬行|\b(?:bipedal|quadrupedal)\b",
}
_BODY_COLORS = {"雪白", "洁白", "灰白", "焦黑", "漆黑", "棕色", "橘色"}
_PROP_DIMENSIONS = {
    "color": r"红色|蓝色|绿色|黄色|白色|黑色|紫色|棕色|灰色|\b(?:red|blue|green|yellow|white|black|purple|brown|grey|gray)\b",
    "marking": r"印着|印有|标记|图案|\b(?:marking|mark|pattern|logo|printed)\b",
    "shape": r"圆形|方形|长方形|三角形|\b(?:round|square|rectangular|triangular)\b",
}


def merge_complementary_appearance(values, *, kind="character"):
    parts = sorted({part.strip() for value in values for part in str(value).split("；") if part.strip()})
    if len(parts) < 2:
        return parts[0] if parts else None
    # A more specific quote may repeat an already verified shorter feature.
    parts = [part for part in parts if not any(part != other and part in other for other in parts)]
    dimensions = set()
    for part in parts:
        rules = _PROP_DIMENSIONS if kind == "prop" else _DIMENSIONS
        matched = {key for key, expression in rules.items() if re.search(expression, part, re.I)}
        if kind == "character" and part in _BODY_COLORS:
            matched = {"body_color"}
        if len(matched) != 1 or dimensions & matched:
            return None
        dimensions.update(matched)
    return "；".join(parts)
