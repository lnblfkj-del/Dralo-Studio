"""Read-only inventory of distinct extraction, identity and candidate obligations."""

import hashlib
import json
from collections import Counter

PATHS = {"fact_grounding": "AR2: isolate unverified fields", "fact_ownership": "AR2: resolve source subject",
         "fact_conflict": "AR2: classify contradictory facts", "default_appearance_conflict": "AR2: separate appearance states",
         "identity_resolution": "AR3: resolve scoped representations"}


def baseline(breakdown):
    candidates = breakdown.get("candidates") or []
    issues = breakdown.get("validation_issues") or []
    rows = []
    for row in candidates:
        attrs = row.get("attributes") or {}
        assigned = [issue for issue in issues if issue.get("candidate_id") == row["candidate_id"]]
        if not row.get("needs_review") and not assigned:
            continue
        rows.append({"candidate_id": row["candidate_id"], "name": row["name"],
                     "requirement_type": row.get("requirement_type", row.get("asset_type")),
                     "selected": row.get("selected", True),
                     "merged_into_candidate_id": row.get("merged_into_candidate_id"),
                     "review_reasons": attrs.get("review_reasons") or [],
                     "next_steps": sorted({PATHS.get(reason, "AR2: inspect field evidence")
                                           for reason in attrs.get("review_reasons") or []}
                                          | ({"AR1: resolve character labels"} if assigned else set())),
                     "identity_reason": attrs.get("identity_match_reason"),
                     "conflict_fields": sorted((attrs.get("identity_fact_conflicts") or {}).keys()),
                     "field_obligations": [{"field": f.get("field"), "origin": f.get("origin"),
                                            "ownership": f.get("ownership_status"),
                                            "source_segment_id": f.get("source_segment_id")}
                                           for f in attrs.get("facts") or [] if f.get("needs_review")],
                     "issues": assigned})
    outputs = [{"scope_key": scope, "job_id": group.get("job_id"), **item}
               for scope, group in (breakdown.get("review_items_by_scope") or {}).items()
               for item in group.get("items") or []]
    outputs = list({(item["scope_key"], item["job_id"], item.get("index")): item for item in outputs}.values())
    for item in outputs:
        item["related_candidate_ids"] = sorted(row["candidate_id"] for row in candidates if row["name"] == item.get("name"))
        item["next_step"] = "AR3: validate corrected structure" if item.get("error_code") == "MODEL_OUTPUT_SCHEMA_INVALID" else "AR3: verify original source evidence"
    fields = Counter(error.get("field", "").rsplit(".", 1)[-1]
                     for item in outputs for error in item.get("field_errors") or [])
    return {"version": "asset-breakdown-baseline.ar0.v1",
            "input_fingerprint": breakdown.get("input_fingerprint"),
            "snapshot_sha256": hashlib.sha256(json.dumps(breakdown, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "counts": {"candidates": len(candidates), "attention_candidates": len(rows),
                       "active_attention_candidates": sum(row["selected"] and not row["merged_into_candidate_id"] for row in rows),
                       "blocking_issues": sum(i.get("severity") == "blocking" for i in issues),
                       "output_review_items": len(outputs)},
            "counting_rule": "Candidate IDs count assets; scope/job/index count rejected outputs. Counts overlap and are not additive.",
            "output_error_fields": dict(sorted(fields.items())), "candidates": rows,
            "output_review_items": outputs, "validation_issues": issues}
