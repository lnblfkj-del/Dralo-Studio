"""Deterministic display/compiler input. Never rewrite dialogue or choose timing."""

import hashlib
import json

from app.schemas.episode_planning import FrozenEpisodePlan, SegmentDetail
from app.schemas.episode_timing import ContentAnalysis
from app.services.episode_planning_contract import (
    fingerprint,
    validate_detail,
    validate_frozen_plan,
)
from app.services.episode_planning_storage import validate_analysis_snapshot


def assemble(
    plan: FrozenEpisodePlan, analysis: ContentAnalysis, details: tuple[SegmentDetail, ...]
) -> dict:
    validate_frozen_plan(plan)
    # Also verify the analysis/shot relationship, not just its serialized hash.
    validate_analysis_snapshot(plan, analysis)
    by_segment = {detail.segment_key: detail for detail in details}
    if len(by_segment) != len(details) or set(by_segment) != {s.key for s in plan.segments}:
        raise ValueError("Assembly requires exactly one detail for every frozen segment")
    sources = {source.key: source for source in plan.sources}
    blocks = {block.key: block for block in analysis.blocks}
    segments, cursor = [], 0
    for segment in plan.segments:
        detail = by_segment[segment.key]
        validate_detail(plan, detail)
        cameras = {shot.key: shot for shot in detail.shots}
        cursor -= segment.overlap_previous_ms
        shots = []
        for shot in segment.shots:
            block = blocks[shot.key]
            shots.append(
                {
                    "key": shot.key,
                    "start_ms": shot.start_ms,
                    "end_ms": shot.end_ms,
                    "timeline_start_ms": cursor + shot.start_ms,
                    "timeline_end_ms": cursor + shot.end_ms,
                    "direction": cameras[shot.key].model_dump(mode="json"),
                    # Includes music instructions as source evidence even when BGM
                    # is off. Consumers must use background_music for generation.
                    "sources": [sources[key].model_dump(mode="json") for key in shot.source_keys],
                    "timing_events": [event.model_dump(mode="json") for event in block.events],
                    "overlap_basis": block.overlap_basis,
                }
            )
        segments.append(
            {
                **segment.model_dump(mode="json"),
                "shots": shots,
                "timeline_start_ms": cursor,
                "timeline_end_ms": cursor + segment.used_duration_ms,
                "entry_state": detail.entry_state,
                "exit_state": detail.exit_state,
                "detail_fingerprint": fingerprint(detail),
            }
        )
        cursor += segment.used_duration_ms
    if cursor != plan.planned_timeline_ms:
        raise ValueError("Assembled timeline differs from frozen plan")
    result = {
        "contract_version": "episode-assembly.v1",
        "plan_fingerprint": fingerprint(plan),
        "episode_id": plan.episode_id,
        "script_revision": plan.script_revision,
        "timing": plan.timing.model_dump(mode="json"),
        "timeline_ms": cursor,
        "segments": segments,
        "video_submission_ready": False,
    }
    # Submission remains gated until the protocol compiler and media preflight
    # consume this contract; a complete text result is not a paid video request.
    digest = hashlib.sha256(
        json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return {**result, "fingerprint": digest}


def read_assembly(plan: FrozenEpisodePlan, analysis: ContentAnalysis, stored: dict) -> dict:
    """Verify a durable projection without requiring expired raw model receipts."""
    try:
        details = tuple(
            SegmentDetail.model_validate_json(
                json.dumps(
                    {
                        "contract_version": "episode-planning.v1",
                        "plan_fingerprint": stored["plan_fingerprint"],
                        "segment_key": segment["key"],
                        "entry_state": segment["entry_state"],
                        "exit_state": segment["exit_state"],
                        "shots": [shot["direction"] for shot in segment["shots"]],
                    }
                )
            )
            for segment in stored["segments"]
        )
        rebuilt = assemble(plan, analysis, details)
    except (KeyError, TypeError) as exc:
        raise ValueError("Stored assembly is incomplete") from exc
    if rebuilt != stored:
        raise ValueError("Stored assembly differs from the frozen plan or detail receipt")
    return rebuilt
