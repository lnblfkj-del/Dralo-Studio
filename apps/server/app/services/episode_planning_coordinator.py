"""Local planning and model-switch operations. No provider or job submissions."""

import json
from dataclasses import dataclass
from time import monotonic

from app.schemas.episode_planning import (
    CONTRACT_VERSION,
    FrozenEpisodePlan,
    PlannedSegment,
    PlannedShot,
    SegmentDetail,
    VideoCapability,
    VideoMode,
)
from app.schemas.episode_timing import ContentAnalysis, ContentBlock, PlanningSources
from app.services.episode_content_timing import content_timing, validate_analysis
from app.services.episode_planning_contract import (
    CompatibilityIssue,
    compatibility_issues,
    fingerprint,
    validate_detail,
    validate_frozen_plan,
)
from app.services.episode_planning_solver import (
    Partition,
    PlanningInfeasibleError,
    PlanningTimeoutError,
    solve_partition,
)


@dataclass(frozen=True)
class PlanningResult:
    plan: FrozenEpisodePlan
    solves: tuple[Partition, ...]
    replaced_segment_keys: tuple[str, ...] = ()


def _mode(capability: VideoCapability, mode_key: str) -> VideoMode:
    mode = next((m for m in capability.modes if m.key == mode_key), None)
    if mode is None:
        raise ValueError("Select an explicitly configured video mode")
    return mode


def _segments(
    blocks: tuple[ContentBlock, ...],
    partition: Partition,
    mode: VideoMode,
    sources: PlanningSources,
    background_music: bool,
) -> tuple[PlannedSegment, ...]:
    units = {unit.key: unit for unit in sources.units}
    segments = []
    for candidate in partition.candidates:
        selected = blocks[candidate.start : candidate.end]
        shots, cursor = [], 0
        for block, duration in zip(selected, candidate.block_durations_ms, strict=True):
            shots.append(
                PlannedShot(
                    key=block.key,
                    source_keys=block.source_keys,
                    start_ms=cursor,
                    end_ms=cursor + duration,
                )
            )
            cursor += duration
        kinds = {units[key].kind for block in selected for key in block.source_keys}
        payload = {
            "mode_key": mode.key,
            "requested_duration_ms": candidate.duration_ms,
            "used_duration_ms": candidate.duration_ms,
            "safe_head_ms": 0,
            "safe_tail_ms": 0,
            "shots": tuple(shots),
            "references": candidate.references,
            "requires_native_dialogue": bool(kinds & {"dialogue", "narration"}),
            "requires_native_audio": bool(kinds & {"dialogue", "narration", "sound"}),
            "background_music": background_music,
        }
        temporary = PlannedSegment(key="pending", **payload)
        segments.append(PlannedSegment(key=f"segment-{fingerprint(temporary)[:24]}", **payload))
    return tuple(segments)


def create_plan(
    *,
    sources: PlanningSources,
    analysis: ContentAnalysis,
    capability: VideoCapability,
    mode_key: str,
    episode_id: int,
    script_revision: int,
    asset_fingerprint: str,
    execution_epoch: str,
    plan_revision: int,
    compiler_version: str,
    editorial_target_ms: int | None,
    background_music: bool,
) -> PlanningResult:
    timing = content_timing(sources, analysis, editorial_target_ms)
    mode = _mode(capability, mode_key)
    partition = solve_partition(analysis.blocks, mode)
    segments = _segments(analysis.blocks, partition, mode, sources, background_music)
    plan = FrozenEpisodePlan(
        contract_version=CONTRACT_VERSION,
        execution_epoch=execution_epoch,
        episode_id=episode_id,
        script_revision=script_revision,
        script_fingerprint=sources.script_fingerprint,
        asset_fingerprint=asset_fingerprint,
        plan_revision=plan_revision,
        compiler_version=compiler_version,
        content_analysis_fingerprint=fingerprint(analysis),
        timing=timing,
        capability=capability,
        sources=sources.units,
        segments=segments,
        planned_timeline_ms=sum(segment.used_duration_ms for segment in segments),
    )
    validate_frozen_plan(plan)
    return PlanningResult(plan, (partition,))


def _switch_copy(plan: FrozenEpisodePlan, target: VideoCapability, mode_key: str):
    """Remap the user's explicitly selected mode for comparison, never infer an alias."""
    mode = _mode(target, mode_key)
    original_modes = {m.key: m for m in plan.capability.modes}
    issues = []
    for segment in plan.segments:
        original = original_modes[segment.mode_key]
        if (original.input_mode, original.aspect_ratio, original.resolution) != (
            mode.input_mode,
            mode.aspect_ratio,
            mode.resolution,
        ):
            issues.append(CompatibilityIssue(segment.key, "mode_parameters_changed"))
    raw = plan.model_dump(mode="json")
    raw["capability"] = target.model_dump(mode="json")
    for segment in raw["segments"]:
        segment["mode_key"] = mode_key
    draft = FrozenEpisodePlan.model_validate_json(json.dumps(raw))
    return draft, tuple(issues) + compatibility_issues(draft, target)


def assess_model_switch(
    plan: FrozenEpisodePlan,
    target: VideoCapability,
    mode_key: str,
) -> tuple[CompatibilityIssue, ...]:
    validate_frozen_plan(plan)
    return _switch_copy(plan, target, mode_key)[1]


def replan_for_model(
    plan: FrozenEpisodePlan,
    sources: PlanningSources,
    analysis: ContentAnalysis,
    target: VideoCapability,
    mode_key: str,
) -> PlanningResult:
    deadline = monotonic() + 2.0
    validate_frozen_plan(plan)
    validate_analysis(sources, analysis)
    if (
        sources.units != plan.sources
        or sources.script_fingerprint != plan.script_fingerprint
        or fingerprint(analysis) != plan.content_analysis_fingerprint
    ):
        raise ValueError("Replanning requires the original frozen content analysis")
    if fingerprint(target) == fingerprint(plan.capability) and all(
        segment.mode_key == mode_key for segment in plan.segments
    ):
        return PlanningResult(plan, ())
    draft, issues = _switch_copy(plan, target, mode_key)
    affected = {issue.segment_key for issue in issues}
    mode = _mode(target, mode_key)
    blocks = {block.key: block for block in analysis.blocks}
    if [shot.key for segment in plan.segments for shot in segment.shots] != list(blocks):
        raise ValueError("Frozen shots differ from the content analysis")
    ranges = [(i, i + 1) for i, segment in enumerate(draft.segments) if segment.key in affected]
    # Expand only when an affected range has no feasible local solution.
    # Overlapping expansions merge before replacing any segment.
    while True:
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        replacements = {}
        for start, end in merged:
            selected_segments = draft.segments[start:end]
            if len({segment.background_music for segment in selected_segments}) != 1:
                raise ValueError(
                    "Replanning across differing music settings requires an explicit choice"
                )
            selected = tuple(
                blocks[shot.key] for segment in selected_segments for shot in segment.shots
            )
            try:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise PlanningTimeoutError("模型切换重规划达到总时间限制，未修改当前计划")
                partition = solve_partition(selected, mode, timeout_seconds=remaining)
            except PlanningInfeasibleError:
                expanded = (max(0, start - 1), min(len(draft.segments), end + 1))
                if expanded == (start, end):
                    raise
                ranges = [r for r in merged if r != (start, end)] + [expanded]
                break
            replacements[start] = (
                end,
                partition,
                _segments(
                    selected,
                    partition,
                    mode,
                    sources,
                    selected_segments[0].background_music,
                ),
            )
        else:
            break
    segments, solves, replaced = [], [], []
    index = 0
    while index < len(draft.segments):
        if index in replacements:
            end, partition, replacement = replacements[index]
            segments.extend(replacement)
            solves.append(partition)
            replaced.extend(s.key for s in plan.segments[index:end])
            index = end
        else:
            segments.append(draft.segments[index])
            index += 1
    raw = draft.model_dump(mode="json")
    raw.update(
        plan_revision=plan.plan_revision + 1,
        segments=[s.model_dump(mode="json") for s in segments],
        planned_timeline_ms=sum(s.used_duration_ms for s in segments),
    )
    result = FrozenEpisodePlan.model_validate_json(json.dumps(raw))
    validate_frozen_plan(result)
    return PlanningResult(result, tuple(solves), tuple(replaced))


def reusable_details(
    original: FrozenEpisodePlan,
    result: PlanningResult,
    details: tuple[SegmentDetail, ...],
) -> tuple[SegmentDetail, ...]:
    """Rebind unchanged detail content; caller records provenance, never re-submits it."""
    updated = result.plan
    if (
        original.sources != updated.sources
        or original.script_revision != updated.script_revision
        or original.script_fingerprint != updated.script_fingerprint
        or original.asset_fingerprint != updated.asset_fingerprint
        or original.execution_epoch != updated.execution_epoch
        or original.episode_id != updated.episode_id
    ):
        raise ValueError("Detail reuse requires the same episode content and assets")
    if len({detail.segment_key for detail in details}) != len(details):
        raise ValueError("Duplicate segment detail")
    original_segments = {s.key: s for s in original.segments}
    new_segments = {s.key: s for s in updated.segments}
    retained = []
    for detail in details:
        validate_detail(original, detail)
        segment = new_segments.get(detail.segment_key)
        if segment is None or detail.segment_key in result.replaced_segment_keys:
            continue
        before = original_segments[detail.segment_key].model_dump(exclude={"mode_key"})
        if before != segment.model_dump(exclude={"mode_key"}):
            raise ValueError("Changed segment cannot reuse frozen detail")
        if any(
            i.segment_key == detail.segment_key
            for i in assess_model_switch(
                original,
                updated.capability,
                segment.mode_key,
            )
        ):
            raise ValueError("Incompatible segment cannot reuse frozen detail")
        raw = detail.model_dump(mode="json")
        raw["plan_fingerprint"] = fingerprint(updated)
        rebound = SegmentDetail.model_validate_json(json.dumps(raw))
        validate_detail(updated, rebound)
        retained.append(rebound)
    return tuple(retained)


def detail_prompt(plan: FrozenEpisodePlan, segment_key: str, analysis: ContentAnalysis) -> str:
    validate_frozen_plan(plan)
    if fingerprint(analysis) != plan.content_analysis_fingerprint:
        raise ValueError("Detail generation requires the frozen content analysis")
    segment = next(s for s in plan.segments if s.key == segment_key)
    units = {unit.key: unit for unit in plan.sources}
    source_keys = [key for shot in segment.shots for key in shot.source_keys]
    block_keys = {shot.key for shot in segment.shots}
    performance = [
        block.model_dump(mode="json") for block in analysis.blocks if block.key in block_keys
    ]
    from app.schemas.episode_planning import SegmentDetail

    return (
        "只填充已冻结镜头的摄影、动作演绎及入场/出场状态，返回 SegmentDetail JSON。"
        "不得新增镜头、片段或改变时长；不要重新输出或改写对白和声音，系统从原文装配。"
        "不得新增剧情、说话人或资产；动作与原文一致，保留衔接。"
        f"计划指纹：{fingerprint(plan)}\n"
        f"冻结片段：{segment.model_dump_json()}\n"
        f"表演时间与并行关系：{json.dumps(performance, ensure_ascii=False)}\n"
        f"原文：{json.dumps([units[k].model_dump(mode='json') for k in source_keys], ensure_ascii=False)}\n"
        f"结构：{json.dumps(SegmentDetail.model_json_schema(), ensure_ascii=False)}"
    )
