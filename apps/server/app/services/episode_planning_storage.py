"""R0 persistence primitives. Transactions and authorization belong to the caller.

Commit the raw receipt before parsing it in a separate transaction. These functions
cannot call a model, migrate an old plan, or start a task.
"""

import hashlib
import json

from app.models.episode_planning import EpisodePlanningRecord, PlanningResponseRecord
from app.schemas.episode_planning import FrozenEpisodePlan, PlanningTask, SegmentDetail
from app.schemas.episode_timing import ContentAnalysis, PlanningSources
from app.services.episode_content_timing import content_timing
from app.services.episode_planning_contract import (
    fingerprint,
    validate_detail,
    validate_frozen_plan,
)
from app.services.episode_planning_solver import frame_boundaries_match


def validate_analysis_snapshot(plan: FrozenEpisodePlan, analysis: ContentAnalysis | None) -> None:
    if plan.content_analysis_fingerprint is None and analysis is None:
        return
    if analysis is None or fingerprint(analysis) != plan.content_analysis_fingerprint:
        raise ValueError("Frozen content analysis missing or changed")
    sources = PlanningSources(script_fingerprint=plan.script_fingerprint, units=plan.sources)
    if content_timing(sources, analysis, plan.timing.editorial_target_ms) != plan.timing:
        raise ValueError("Frozen content estimate changed")
    shots = [shot for segment in plan.segments for shot in segment.shots]
    if [shot.key for shot in shots] != [block.key for block in analysis.blocks]:
        raise ValueError("Frozen shot structure differs from analysis")
    for shot, block in zip(shots, analysis.blocks, strict=True):
        if shot.source_keys != block.source_keys or not block.duration(
            "estimated_ms"
        ) <= shot.end_ms - shot.start_ms <= block.duration("maximum_ms"):
            raise ValueError("Frozen performance timing differs from analysis")
    blocks = {block.key: block for block in analysis.blocks}
    modes = {mode.key: mode for mode in plan.capability.modes}
    for segment in plan.segments:
        if not frame_boundaries_match(
            tuple(blocks[shot.key] for shot in segment.shots), modes[segment.mode_key]
        ):
            raise ValueError("Frozen segment frame bindings differ from its boundaries")
        cursor = 0
        for shot in segment.shots:
            if shot.start_ms != cursor:
                raise ValueError("Frozen shots must form a continuous segment timeline")
            cursor = shot.end_ms
        if cursor != segment.used_duration_ms:
            raise ValueError("Frozen shots must cover the segment usage interval")
        references = tuple(
            {
                (ref.media_id, ref.role): ref
                for shot in segment.shots
                for ref in blocks[shot.key].references
            }.values()
        )
        if references != segment.references:
            raise ValueError("Frozen segment references differ from content bindings")


def new_plan_record(
    plan: FrozenEpisodePlan,
    *,
    workspace_id: str | None,
    analysis: ContentAnalysis | None = None,
) -> EpisodePlanningRecord:
    validate_frozen_plan(plan)
    validate_analysis_snapshot(plan, analysis)
    return EpisodePlanningRecord(
        workspace_id=workspace_id,
        episode_id=plan.episode_id,
        execution_epoch=plan.execution_epoch,
        revision=plan.plan_revision,
        fingerprint=fingerprint(plan),
        snapshot=plan.model_dump(mode="json"),
        analysis_snapshot=analysis.model_dump(mode="json") if analysis is not None else None,
    )


def read_plan(record: EpisodePlanningRecord) -> FrozenEpisodePlan:
    # JSON deserialization is intentional: tuple fields are arrays on the wire.
    plan = FrozenEpisodePlan.model_validate_json(json.dumps(record.snapshot))
    validate_frozen_plan(plan)
    if (
        fingerprint(plan) != record.fingerprint
        or plan.episode_id != record.episode_id
        or plan.plan_revision != record.revision
        or plan.execution_epoch != record.execution_epoch
    ):
        raise ValueError("Stored plan identity or fingerprint mismatch")
    analysis = (
        ContentAnalysis.model_validate_json(json.dumps(record.analysis_snapshot))
        if record.analysis_snapshot is not None
        else None
    )
    validate_analysis_snapshot(plan, analysis)
    return plan


def read_content_analysis(record: EpisodePlanningRecord) -> ContentAnalysis:
    read_plan(record)
    if record.analysis_snapshot is None:
        raise ValueError("Content-driven planning requires a frozen content analysis")
    return ContentAnalysis.model_validate_json(json.dumps(record.analysis_snapshot))


def new_response_record(
    record: EpisodePlanningRecord,
    task: PlanningTask,
    raw: str,
) -> PlanningResponseRecord:
    plan = read_plan(record)
    if (
        record.id is None
        or task.plan_fingerprint != record.fingerprint
        or task.execution_epoch != record.execution_epoch
        or task.state not in {"submitted", "result_unknown"}
        or task.segment_key not in {segment.key for segment in plan.segments}
    ):
        raise ValueError("Response does not belong to this submitted plan task")
    return PlanningResponseRecord(
        workspace_id=record.workspace_id,
        plan_id=record.id,
        request_id=task.request_id,
        segment_key=task.segment_key,
        raw_response=raw,
        response_fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        status="available",
        detail=None,
    )


def prepare_saved_detail(
    record: EpisodePlanningRecord,
    response: PlanningResponseRecord,
) -> SegmentDetail:
    plan = read_plan(record)
    if response.plan_id != record.id or response.workspace_id != record.workspace_id:
        raise ValueError("Response belongs to another plan or workspace")
    if (
        hashlib.sha256(response.raw_response.encode("utf-8")).hexdigest()
        != response.response_fingerprint
    ):
        raise ValueError("Raw response integrity check failed")
    detail = SegmentDetail.model_validate_json(response.raw_response)
    if detail.segment_key != response.segment_key:
        raise ValueError("Response targets a different segment")
    validate_detail(plan, detail)
    return detail
