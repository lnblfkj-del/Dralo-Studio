"""Keep authored shot semantics when changing segment boundaries."""

from copy import deepcopy

from app.core.errors import ValidationError
from app.services.asset_production_service import fingerprint
from app.services.segment_script_semantics import STRUCTURED_SCRIPT_KEY


def slice_parameters(parameters, shot_ids):
    result = deepcopy(parameters or {})
    script = result.get(STRUCTURED_SCRIPT_KEY)
    if not isinstance(script, dict):
        return result
    original_ids = script["source_shot_ids"]
    if script.get("editor_document"):
        content, current = [], None
        for paragraph in script["editor_document"].get("content", []):
            markers = [n for n in paragraph.get("content", []) if n.get("type") == "scriptTool" and (n.get("attrs") or {}).get("kind") == "shot"]
            if markers:
                current = markers[0]["attrs"].get("sourceShotId") or current
            if current is None or current in shot_ids:
                content.append(paragraph)
        script["editor_document"]["content"] = content
    original_performances = script["performances"]
    script["source_shot_ids"] = list(shot_ids)
    for key in ("camera", "performances", "dialogue"):
        script[key] = [row for row in script.get(key, []) if row["shot_id"] in shot_ids]
    script["audio"] = {key: [row for row in rows if row["shot_id"] in shot_ids]
                       for key, rows in script.get("audio", {}).items()}
    if shot_ids[0] != original_ids[0]:
        previous = original_ids[original_ids.index(shot_ids[0]) - 1]
        script["entry_state"] = next(row["action"] for row in original_performances if row["shot_id"] == previous)
    if shot_ids[-1] != original_ids[-1]:
        script["exit_state"] = next(row["action"] for row in original_performances if row["shot_id"] == shot_ids[-1])
    return result


def split_parameters(item, shot_ids):
    result = slice_parameters(item.get("parameters") or {}, shot_ids)
    if not isinstance(result.get(STRUCTURED_SCRIPT_KEY), dict) and str(item.get("prompt") or "").strip():
        result["text_split_review_required"] = True
    return result


def needs_exclusion_review(plan):
    parameters = plan.get("parameters") or {}
    excluded = parameters.get("excluded_shot_ids", [])
    return bool(excluded and parameters.get("confirmed_excluded_shot_ids") != excluded)


def merge_parameters(items):
    result = deepcopy(items[0].get("parameters") or {})
    scripts = [(item.get("parameters") or {}).get(STRUCTURED_SCRIPT_KEY) for item in items]
    if not any(isinstance(script, dict) for script in scripts):
        if any((item.get("parameters") or {}).get("text_split_review_required") for item in items):
            result["text_split_review_required"] = True
        return result
    if not all(isinstance(script, dict) for script in scripts):
        raise ValidationError("结构化脚本与纯文本脚本不能直接合并，请先统一编辑形式")
    if any(script.get("editor_document") for script in scripts):
        if not all(script.get("editor_document") for script in scripts):
            raise ValidationError("请先保存各片段正文，再合并新旧编辑格式")
    merged = deepcopy(scripts[0])
    if merged.get("editor_document"):
        merged["editor_document"]["content"] = [deepcopy(p) for s in scripts for p in s["editor_document"].get("content", [])]
    merged["source_shot_ids"] = [shot_id for script in scripts for shot_id in script["source_shot_ids"]]
    for key in ("camera", "performances", "dialogue"):
        merged[key] = [deepcopy(row) for script in scripts for row in script.get(key, [])]
    merged["audio"] = {key: [deepcopy(row) for script in scripts for row in script.get("audio", {}).get(key, [])]
                       for key in ("music", "ambience", "sound_effects")}
    merged["exit_state"] = scripts[-1]["exit_state"]
    result[STRUCTURED_SCRIPT_KEY] = merged
    return result


def merge_refs(items):
    result = deepcopy(items[0].get("refs") or {})
    lineages = {item["lineage_key"] for item in items}
    for item in items[1:]:
        dependency = (item.get("refs") or {}).get("continuity")
        if dependency and dependency.get("source_lineage_key") not in lineages:
            raise ValidationError("待合并片段包含不同的外部尾帧依赖，请先解除后再合并")
    for key in (
        "asset_bindings", "reference_media_ids", "unresolved_assets",
        "unmatched_assets", "ignored_unmatched_assets",
    ):
        values = [value for item in items for value in (item.get("refs") or {}).get(key, [])]
        result[key] = list({fingerprint(value): deepcopy(value) for value in values}.values())
    return result
