"""Keep independently evidenced costume facets and temporary actor states distinct."""

# ruff: noqa: RUF001

import re

_FACETS = {
    "hair": r"头发|发型|发髻|刘海|须发|\bhair\b",
    "clothing": r"道袍|衣|裙|裤|外套|服装|鞋|\b(?:clothes|coat|robe|shirt|trousers)\b",
    "injury": r"伤口|淤青|流血|骨折|\b(?:wound|bruise|bleeding)\b",
}
_CHANGED = re.compile(r"炸成|炸起|炸碎|烧成|烧毁|烧焦|浸湿|湿透|撕破|撕裂|破损|受伤|血染|换上|换成|散乱|\b(?:burnt|torn|soaked|injured)\b", re.I)


def facet(value):
    matches = [key for key, expression in _FACETS.items() if re.search(expression, value, re.I)]
    return matches[0] if len(matches) == 1 else None


def merge_stage_facts(facts):
    """Only simultaneous, verified facets from one exact source unit may combine."""
    if not facts or any(not row.get("quote_verified") for row in facts):
        return None
    locations = {(row.get("source_segment_id"), row.get("source_unit_id") or
                  (row.get("start"), row.get("end"))) for row in facts}
    if len(locations) != 1:
        return None
    values = sorted({row["value"] for row in facts})
    dimensions = [facet(value) for value in values]
    if None in dimensions or len(set(dimensions)) != len(values):
        return None
    return "；".join(values)


def temporary_character_fact(fact, scoped_text):
    if fact.field not in {"appearance", "costume", "hair", "makeup", "injury", "stage", "story_state"}:
        return False
    # A nearby but unrelated event must not reclassify a stable feature.
    clauses = re.split(r"[。！？!?；;，,]", scoped_text)
    return any(fact.value in clause and _CHANGED.search(clause) for clause in clauses) or bool(_CHANGED.search(fact.value))
