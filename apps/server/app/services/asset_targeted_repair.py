"""Explicitly paid item repair preserves the frozen, validated batch baseline."""

import json
from copy import deepcopy

from app.core.errors import ModelOutputBusinessValidationError
from app.services.creation_breakdown_planning import REQUIREMENT_RESULT_KEYS
from app.services.director_output_transport import digest
from app.services.structured_output_service import parse_json_document


def freeze_repair(baseline, diagnostics):
    rejected = diagnostics.get("rejected_items") or []
    if not baseline or not rejected:
        return None
    targets = [(row.get("kind"), row.get("name")) for row in rejected]
    existing = {(kind, row["name"]) for kind, group in REQUIREMENT_RESULT_KEYS.items()
                for row in baseline.get(group, [])}
    # Ambiguous identities need a range rebuild, not a guessed correspondence.
    if (any(kind not in REQUIREMENT_RESULT_KEYS or not isinstance(name, str) or not name.strip()
            for kind, name in targets) or len(set(targets)) != len(targets)
            or existing.intersection(targets)):
        return None
    result = {"version": "asset-item-repair.v1", "baseline": deepcopy(baseline),
              "targets": [{"kind": kind, "name": name, "field_errors": row.get("field_errors", [])}
                          for (kind, name), row in zip(targets, rejected, strict=True)]}
    return {**result, "sha256": digest(result)}


def parse_repair(text, *, repair, diagnostics_out, **kwargs):
    from app.services.asset_extraction_partial import parse_batch
    if repair.get("sha256") != digest({k: v for k, v in repair.items() if k != "sha256"}):
        raise ModelOutputBusinessValidationError("定向修复基线不一致，未覆盖已通过素材")
    document, _ = parse_json_document(text, "资产定向修复", reject_duplicate_keys=True)
    if not isinstance(document, dict) or set(document) != {"assets"} or not isinstance(document["assets"], list):
        raise ModelOutputBusinessValidationError("定向修复响应结构无效，已保留原候选")
    targets = {(row["kind"], row["name"]) for row in repair["targets"]}
    returned = set()
    for row in document["assets"]:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str) or not isinstance(row.get("name"), str):
            raise ModelOutputBusinessValidationError("定向修复缺少条目身份，已保留原候选")
        key = (row["kind"], row["name"])
        if key not in targets or key in returned:
            raise ModelOutputBusinessValidationError("定向修复包含未请求或重复的条目，未覆盖已通过素材")
        returned.add(key)
    # Missing targets remain explicitly failed, never silently counted as done.
    document["assets"].extend({"kind": kind, "name": name} for kind, name in sorted(targets - returned))
    from app.services.creation_breakdown_planning import visual_asset_key_context
    kwargs["visual_keys"] = [*(kwargs.get("visual_keys") or []), *visual_asset_key_context([repair["baseline"]])]
    names = {row["name"] for row in repair["baseline"].get("characters", [])}
    names.update(row["name"] for row in document["assets"] if row.get("kind") == "character")
    parsed = parse_batch(json.dumps(document, ensure_ascii=False), diagnostics_out=diagnostics_out,
                         _character_names=names, **kwargs)
    result = deepcopy(repair["baseline"])
    retained = 0
    for group in REQUIREMENT_RESULT_KEYS.values():
        retained += len(result.get(group, []))
        result.setdefault(group, []).extend(parsed.get(group, []))
    rejected = diagnostics_out.get("rejected_items") or []
    diagnostics_out.update(repair_version=repair["version"],
                           retained_items=retained + sum(len(parsed.get(group, [])) for group in REQUIREMENT_RESULT_KEYS.values()),
                           total_items=retained + len(targets), rejected_items=rejected)
    return result
