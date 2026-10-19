"""Isolate invalid items in complete responses; never repair truncated JSON."""

import json
import re

from app.core.errors import (
    ConflictError,
    ModelOutputBusinessValidationError,
    ModelOutputSchemaError,
)
from app.services.creation_breakdown_planning import (
    AUDIO_REQUIREMENT_TYPES,
    REQUIREMENT_RESULT_KEYS,
)
from app.services.asset_json_repair import parse_asset_document

ITEM_ERRORS = (ModelOutputBusinessValidationError, ModelOutputSchemaError)


def defers_item_review(parameters, diagnostics):
    """Only complete responses with verified survivors can settle for review."""
    return bool((parameters.get("asset_necessity_policy") or {}).get("candidate_review")
                and diagnostics.get("rejected_items") and diagnostics.get("retained_items", 0) > 0)


def parse_batch(text, *, sources, kinds, visual_keys, diagnostics_out, necessity_policy, _character_names=None):
    """Keep the normal fast path, inspecting individual rows only after a failure."""
    from app.services.asset_extraction_contract import _source_map, parse_extraction, response_schema

    try:
        return parse_extraction(text, sources=sources, kinds=kinds, visual_keys=visual_keys,
                                diagnostics_out=diagnostics_out, necessity_policy=necessity_policy,
                                _character_names=_character_names)
    except ITEM_ERRORS:
        if not (necessity_policy or {}).get("item_failures"):
            raise
        document, diagnostics = parse_asset_document(
            text, response_schema(sources=sources, necessity_policy=necessity_policy),
        )
        # Unknown root fields, an excessive list, and invalid frozen source data
        # are document failures, not evidence that its individual rows are safe.
        if (not isinstance(document, dict) or set(document) != {"assets"}
                or not isinstance(document["assets"], list) or len(document["assets"]) > 1000):
            raise
        _source_map(sources)

    rows = document["assets"]
    names = {row["name"].strip() for row in rows if isinstance(row, dict)
             and row.get("kind") == "character" and isinstance(row.get("name"), str) and row["name"].strip()}
    names.update(_character_names or [])
    references = list(visual_keys or [])
    result = {"reply": "本批部分资产待恢复", **{key: [] for key in REQUIREMENT_RESULT_KEYS.values()}}
    rejected, changes, source_only = [], [], []
    ordered = sorted(enumerate(rows), key=lambda pair: isinstance(pair[1], dict)
                     and isinstance(pair[1].get("kind"), str) and pair[1]["kind"] in AUDIO_REQUIREMENT_TYPES)
    for index, row in ordered:
        item_diagnostics = {}
        try:
            parsed = parse_extraction(json.dumps({"assets": [row]}, ensure_ascii=False), sources=sources,
                                      kinds=kinds, visual_keys=references, diagnostics_out=item_diagnostics,
                                      necessity_policy=necessity_policy, _character_names=names)
        except ITEM_ERRORS as exc:
            details = exc.details or {}
            errors = details.get("field_errors") or [{"field": details.get("field", "assets.0"),
                                                       "reason": exc.message}]
            errors = [{"field": re.sub(r"^assets\.0(?=\.|$)", f"assets.{index}", str(error.get("field", ""))),
                       "reason": str(error.get("reason", exc.message))[:240]} for error in errors[:12]]
            rejected.append({"index": index, "name": str(row.get("name", ""))[:200] if isinstance(row, dict) else "",
                             "kind": str(row.get("kind", ""))[:40] if isinstance(row, dict) else "",
                             "declared_necessity": (row.get("necessity") or {}).get("reasons", [])
                             if isinstance(row, dict) and isinstance(row.get("necessity"), dict) else [],
                             "error_code": exc.code, "field_errors": errors})
            continue
        changes.extend({**change, "asset_index": index} for change in item_diagnostics.get("changes", []))
        source_only.extend({**entry, "index": index} for entry in item_diagnostics.get("source_only_items", []))
        for kind, key in REQUIREMENT_RESULT_KEYS.items():
            result[key].extend(parsed[key])
            if kind not in AUDIO_REQUIREMENT_TYPES:
                references.extend({"asset_key": entry["attributes"]["asset_key"], "name": entry["name"],
                                   "aliases": entry["aliases"], "requirement_type": kind,
                                   "episode_numbers": entry["episode_numbers"]} for entry in parsed[key])
    diagnostics_out.clear()
    diagnostics_out.update({**diagnostics, "normalization_version": "asset-source-shape.v1", "changes": changes,
                            "item_isolation_version": "retain_valid.v1", "rejected_items": rejected,
                            "source_only_items": source_only,
                            "retained_items": sum(len(result[key]) for key in REQUIREMENT_RESULT_KEYS.values()),
                            "total_items": len(rows)})
    return result


async def settle_partial_batch(session, job, worker_id):
    """Commit candidates and a failed job together, keeping its raw response recoverable."""
    if job.target_type != "script_asset_breakdown_batch":
        return False
    diagnostics = (job.payload or {}).get("asset_extraction_diagnostics") or {}
    rejected = diagnostics.get("rejected_items") or []
    if not rejected:
        return False
    if defers_item_review((job.payload or {}).get("parameters") or {}, diagnostics):
        return False
    from app.services.creation_finalize_failures import mark_creation_job_failed
    from app.services.job_state_result_service import mark_failed

    errors = [error for row in rejected for error in row["field_errors"]][:12]
    message = f"已保留 {diagnostics['retained_items']} 条有效候选，{len(rejected)} 条待恢复；未重新调用模型"
    if not await mark_failed(session, job.id, worker_id, "MODEL_OUTPUT_SCHEMA_INVALID", message,
                             provider_diagnostic={"field_errors": errors,
                                                  "invalid_fields": [error["field"] for error in errors]}):
        raise ConflictError("任务状态已变化，部分资产未写入")
    job.payload = {**job.payload, "response_recovery": {
        **dict(job.payload.get("response_recovery") or {}), "status": "available",
        "last_reprocess_error_code": "MODEL_OUTPUT_SCHEMA_INVALID", "last_reprocess_error_message": message,
    }}
    await mark_creation_job_failed(session, job)
    return True
