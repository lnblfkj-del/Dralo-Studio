"""Background ensembles remain in the script without requiring reference assets."""

# ruff: noqa: RUF001

import re

VERSION = "background-ensembles.v1"
_ENSEMBLE = re.compile(r"(?:弟子|修士|护卫|侍卫|守卫|卫兵|士兵|死士|杂役|群众|人群|观众|路人|村民|百姓|随从|宾客)")


def optional_ensemble(name, form, reasons, facts=()):
    return (form in {"crowd", "fixed_group"} and bool(_ENSEMBLE.search(name or ""))
            and bool(reasons) and set(reasons) <= {"visible_performer"} and not facts)


def script_only_reason(asset, policy, usages, sources):
    if (not policy.get("audio_only_crowds") or asset.kind != "character"
            or asset.character_form not in {"crowd", "fixed_group"}
            or not usages or any(row.get("needs_review") for row in usages)
            or set(asset.necessity.reasons) != {"visible_performer"} or asset.facts):
        return None
    audio_only = True
    for usage in usages:
        text = sources[usage["source_segment_id"]]["text"]
        start = text.find(usage["source_excerpt"])
        line = text[text.rfind("\n", 0, start) + 1:].splitlines()[0].strip()
        if not re.match(r"^(?:【)?(?:环境声|环境音|音效|旁白|广播)(?:】[：:]?|[：:])", line):
            audio_only = False
    if audio_only:
        return "audio_only_background_crowd"
    if optional_ensemble(asset.group_label or asset.name, asset.character_form, asset.necessity.reasons):
        return "background_ensemble"
    return None
