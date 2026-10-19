"""Recover invalid saved items locally, preserving batches, user choices and receipts."""

# ruff: noqa: RUF001

import asyncio
import hashlib
import json
from copy import deepcopy

from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    ModelOutputBusinessValidationError,
    ModelOutputSchemaError,
)
from app.models import Job, JobTextResponse
from app.services.asset_extraction_contract import parse_extraction, response_schema
from app.services.asset_extraction_output import validate_boundary
from app.services.asset_extraction_registry import candidate_id, project_registry
from app.services.asset_identity_variants import can_complete, descriptor
from app.services.asset_json_repair import parse_asset_document
from app.services.creation_breakdown_planning import (
    AUDIO_REQUIREMENT_TYPES,
    REQUIREMENT_RESULT_KEYS,
)

VERSION = "asset-output-organization.ar3.v1"


def _unique(rows):
    return list({json.dumps(row, ensure_ascii=False, sort_keys=True): row for row in rows}.values())


def _choice_hash(candidates):
    choices = [{key: row.get(key) for key in ("candidate_id", "name", "selected", "merged_into_candidate_id", "matched_asset_id")}
               | {"manual_reviews": (row.get("attributes") or {}).get("manual_production_reviews")}
               for row in candidates]
    return hashlib.sha256(json.dumps(choices, sort_keys=True).encode()).hexdigest()


def _attach(candidates, entry, kind, breakdown):
    attrs = entry["attributes"]
    named = [row for row in candidates if row.get("requirement_type", row.get("asset_type")) == kind
             and descriptor(kind, row)[0] == descriptor(kind, entry)[0]]
    if kind == "character":
        group_fields = ("character_form", "group_label", "group_size")
        named = [row for row in named if all((row.get("attributes") or {}).get(field) == attrs.get(field) for field in group_fields)]
        if attrs.get("character_form") in {"crowd", "fixed_group"}:
            scopes = {u.get("source_segment_id") for u in entry["usage_records"]}
            named = [row for row in named if scopes & {u.get("source_segment_id") for u in row.get("usage_records") or []}]
    else:
        named = [row for row in named if can_complete(kind, descriptor(kind, row)[1], descriptor(kind, entry)[1])]
    if len(named) > 1:
        raise ModelOutputBusinessValidationError("本地恢复命中多个资产身份，保留待核对记录")
    if named:
        target = named[0]
        visited = set()
        while target.get("merged_into_candidate_id"):
            if target["candidate_id"] in visited:
                raise ConflictError("人工合并关系存在循环，未自动整理")
            visited.add(target["candidate_id"])
            target = next((row for row in candidates if row["candidate_id"] == target["merged_into_candidate_id"]), None)
            if target is None or target.get("requirement_type", target.get("asset_type")) != kind:
                raise ConflictError("人工合并目标已变化，未自动整理")
        # An explicit exclusion/merge remains a user decision, not a recovery target.
        if not target.get("selected", True):
            return {"candidate_id": target["candidate_id"], "status": "preserved_user_exclusion"}
        target["episode_numbers"] = sorted(set(target.get("episode_numbers") or []) | set(entry["episode_numbers"]))
        target["usage_records"] = _unique([*(target.get("usage_records") or []), *entry["usage_records"]])
        target["aliases"] = list(dict.fromkeys([*(target.get("aliases") or []), *entry.get("aliases", [])]))
        for field in ("facts", "necessity_records", "appearance_states"):
            if attrs.get(field):
                target["attributes"][field] = _unique([*(target["attributes"].get(field) or []), *attrs[field]])
        return {"candidate_id": target["candidate_id"], "status": "attached_verified_evidence"}
    context = breakdown.get("production_context") or {}
    projected = project_registry({REQUIREMENT_RESULT_KEYS[kind]: [entry]}, context=context, existing=[])[REQUIREMENT_RESULT_KEYS[kind]][0]
    attrs = projected["attributes"]
    identifier = candidate_id(attrs)
    if any(row["candidate_id"] == identifier for row in candidates):
        raise ModelOutputBusinessValidationError("恢复候选身份重复，需要核对关联")
    asset_type = "voice" if kind in AUDIO_REQUIREMENT_TYPES else kind
    candidates.append({**projected, "candidate_id": identifier, "asset_type": asset_type,
                       "requirement_type": kind, "selected": True, "matched_asset_id": None,
                       "needs_review": bool(attrs.get("needs_review")), "readiness_status": "source_review" if attrs.get("needs_review") else "material_missing",
                       "source_script_revisions": deepcopy(breakdown.get("source_script_revisions") or {}),
                       "input_fingerprint": breakdown.get("input_fingerprint"), "source_kind": context.get("source_kind"),
                       "source_name": context.get("source_name"), "source_records": [
                           {"kind": "episode", "episode_number": number, "source_kind": "formal_script"}
                           for number in projected["episode_numbers"]]})
    return {"candidate_id": identifier, "status": "recovered_candidate"}


async def recover_saved_items(session, item, breakdown):
    if breakdown.get("asset_extraction_contract_version") != 3 or not breakdown.get("review_items_by_scope"):
        return
    audit = deepcopy(breakdown.get("output_organization") or {"version": VERSION, "records": {}})
    candidates = deepcopy(breakdown.get("candidates") or [])
    pending = deepcopy(breakdown["review_items_by_scope"])
    attempts = 0
    unresolved_attempts = []
    for scope, group in list(pending.items()):
        if scope not in (breakdown.get("batches") or {}):
            raise ConflictError("该范围尚未完整保存，不能自动整理为完成")
        job = await session.get(Job, group["job_id"])
        if job is None or job.parent_job_id != breakdown.get("parent_job_id") or job.target_id != item.id:
            raise ConflictError("保存响应与当前拆解范围不一致，未自动整理")
        params = (job.payload or {}).get("parameters") or {}
        if params.get("scope_key") != scope or params.get("asset_extraction_contract_version") != 3:
            raise ConflictError("保存响应的来源范围已变化，未自动整理")
        validate_boundary(job.payload)
        stored = await session.scalar(select(JobTextResponse).where(JobTextResponse.job_id == job.id).order_by(JobTextResponse.id.desc()).limit(1))
        if stored is None:
            continue
        raw_hash = hashlib.sha256(stored.response_text.encode()).hexdigest()
        if raw_hash != stored.response_sha256:
            raise ConflictError("保存响应校验失败，未自动整理")
        sources = params.get("source_segments") or []
        document, _ = parse_asset_document(stored.response_text, response_schema(sources=sources, necessity_policy=params["asset_necessity_policy"]))
        remaining = []
        for rejected in group["items"]:
            index = rejected["index"]
            receipt = f"{scope}:{job.id}:{index}:{raw_hash}:{VERSION}"
            if (receipt in audit["records"] and audit["records"][receipt]["status"] == "unresolved"
                    and audit["records"][receipt].get("choice_sha256") == _choice_hash(candidates)):
                remaining.append(rejected)
                continue
            if attempts >= 64:
                remaining.append(rejected)
                continue
            attempts += 1
            await asyncio.sleep(0)
            if not isinstance(document, dict) or not isinstance(document.get("assets"), list) or not 0 <= index < len(document["assets"]):
                raise ConflictError("保存条目索引已变化，未自动整理")
            row = document["assets"][index]
            diagnostics = {}
            try:
                parsed = parse_extraction(json.dumps({"assets": [row]}, ensure_ascii=False), sources=sources,
                    kinds=params["requirement_types"], visual_keys=params.get("visual_asset_keys") or [],
                    diagnostics_out=diagnostics, necessity_policy=params["asset_necessity_policy"],
                    _character_names={r["name"] for r in candidates if r.get("asset_type") == "character"})
                # Build on a private list so an ambiguous attachment cannot partially mutate pending results.
                trial = deepcopy(candidates)
                targets = [_attach(trial, entry, kind, breakdown) for kind, key in REQUIREMENT_RESULT_KEYS.items() for entry in parsed[key]]
            except (ModelOutputSchemaError, ModelOutputBusinessValidationError) as exc:
                remaining.append(rejected)
                audit["records"][receipt] = {"status": "unresolved", "job_id": job.id, "index": index,
                    "raw_sha256": raw_hash, "error_code": exc.code, "message": exc.message, "original_review": rejected}
                unresolved_attempts.append(receipt)
                continue
            candidates = trial
            audit["records"][receipt] = {"status": "resolved", "job_id": job.id, "index": index,
                "raw_sha256": raw_hash, "targets": targets, "changes": diagnostics.get("changes", []),
                "source_only_items": diagnostics.get("source_only_items", []), "model_called": False, "original_review": rejected}
        if remaining:
            pending[scope]["items"] = remaining
        else:
            pending.pop(scope)
    choice_hash = _choice_hash(candidates)
    for receipt in unresolved_attempts:
        audit["records"][receipt]["choice_sha256"] = choice_hash
    breakdown["candidates"] = candidates
    breakdown["review_items_by_scope"] = pending
    breakdown["output_organization"] = audit
    breakdown["coverage_status"] = "requires_review" if pending else "not_exhaustively_verified"
