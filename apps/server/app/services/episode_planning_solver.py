"""Bounded CP-SAT partitioning of immutable, contiguous performance blocks."""

from collections import Counter
from dataclasses import dataclass
from time import monotonic

from ortools.sat.python import cp_model

from app.schemas.episode_planning import DurationRule, MediaReference, VideoMode
from app.schemas.episode_timing import ContentBlock


class PlanningInfeasibleError(ValueError):
    """The supplied content and mode have no complete legal partition."""


class PlanningTimeoutError(RuntimeError):
    """No feasible result established before the bounded solver deadline."""


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    duration_ms: int
    block_durations_ms: tuple[int, ...]
    references: tuple[MediaReference, ...]


@dataclass(frozen=True)
class Partition:
    candidates: tuple[Candidate, ...]
    status: str
    elapsed_ms: int
    candidate_count: int
    objective: str = "minimum_requests_then_performance_slack"


def legal_duration(rule: DurationRule, required: int) -> int | None:
    if rule.kind == "discrete":
        return next((duration for duration in rule.values_ms if duration >= required), None)
    assert rule.minimum_ms is not None and rule.maximum_ms is not None and rule.step_ms is not None
    steps = max(0, (required - rule.minimum_ms + rule.step_ms - 1) // rule.step_ms)
    duration = rule.minimum_ms + steps * rule.step_ms
    return duration if duration <= rule.maximum_ms else None


def _references(blocks: tuple[ContentBlock, ...], mode: VideoMode):
    if not frame_boundaries_match(blocks, mode):
        return None
    refs = tuple({(r.media_id, r.role): r for block in blocks for r in block.references}.values())
    counts = Counter(ref.role for ref in refs)
    limits = {limit.role: limit for limit in mode.reference_limits}
    if (
        set(counts) - set(limits)
        or len(refs) > mode.max_total_references
        or any(not limit.minimum <= counts[role] <= limit.maximum for role, limit in limits.items())
    ):
        return None
    return refs


def frame_boundaries_match(blocks: tuple[ContentBlock, ...], mode: VideoMode) -> bool:
    frames = [
        (index, ref)
        for index, block in enumerate(blocks)
        for ref in block.references
        if ref.role in {"first_frame", "last_frame"}
    ]
    if mode.input_mode not in {"first_frame", "first_last_frame"}:
        return not frames
    if not blocks or any(
        ref.role not in {"first_frame", "last_frame"}
        for block in blocks
        for ref in block.references
    ):
        return False
    first = [(index, ref) for index, ref in frames if ref.role == "first_frame"]
    last = [(index, ref) for index, ref in frames if ref.role == "last_frame"]
    return (
        len(first) == 1
        and first[0][0] == 0
        and (
            len(last) == 1 and last[0][0] == len(blocks) - 1
            if mode.input_mode == "first_last_frame"
            else not last
        )
    )


def _candidates(
    blocks: tuple[ContentBlock, ...],
    mode: VideoMode,
    deadline: float,
) -> tuple[Candidate, ...]:
    candidates = []
    for start in range(len(blocks)):
        if start and not blocks[start - 1].boundary_after:
            continue
        for end in range(start + 1, min(len(blocks), start + mode.max_shots) + 1):
            if monotonic() >= deadline or len(candidates) >= 20_000:
                raise PlanningTimeoutError("候选规划达到时间或规模限制，尚未完成求解")
            selected = blocks[start:end]
            if selected[-1].scene_key != selected[0].scene_key:
                break
            if not selected[-1].boundary_after:
                continue
            durations = [b.duration("estimated_ms") for b in selected]
            duration = legal_duration(mode.durations, sum(durations))
            if duration is None:
                break
            spare = duration - sum(durations)
            if spare > sum(b.duration("maximum_ms") for b in selected) - sum(durations):
                continue
            refs = _references(selected, mode)
            if refs is None:
                continue
            # Expand only within documented natural performance uncertainty.
            # Never invent a safe trim region in generated media.
            for index in reversed(range(len(selected))):
                extra = min(spare, selected[index].duration("maximum_ms") - durations[index])
                durations[index] += extra
                spare -= extra
            candidates.append(Candidate(start, end, duration, tuple(durations), refs))
    return tuple(candidates)


def solve_partition(
    blocks: tuple[ContentBlock, ...],
    mode: VideoMode,
    *,
    timeout_seconds: float = 2.0,
) -> Partition:
    if not blocks or len(blocks) > 500:
        raise ValueError("Expected 1 to 500 content blocks")
    if not 0 < timeout_seconds <= 10:
        raise ValueError("Solver timeout must be in (0, 10] seconds")
    started = monotonic()
    candidates = _candidates(blocks, mode, started + timeout_seconds)
    model = cp_model.CpModel()
    chosen = [model.new_bool_var(f"edge-{i}") for i in range(len(candidates))]
    # A unit flow on this forward-only graph covers every block once in order.
    outgoing = [[] for _ in range(len(blocks) + 1)]
    incoming = [[] for _ in range(len(blocks) + 1)]
    for variable, candidate in zip(chosen, candidates, strict=True):
        outgoing[candidate.start].append(variable)
        incoming[candidate.end].append(variable)
    for boundary in range(len(blocks) + 1):
        model.add(
            sum(outgoing[boundary]) - sum(incoming[boundary])
            == (1 if boundary == 0 else -1 if boundary == len(blocks) else 0)
        )
    # Lexicographic dominance is bounded, not a tunable arbitrary weight.
    max_timeline = sum(block.duration("maximum_ms") for block in blocks)
    model.minimize(
        sum(chosen[i] * (max_timeline + 1 + c.duration_ms) for i, c in enumerate(candidates))
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    remaining = timeout_seconds - (monotonic() - started)
    if remaining <= 0:
        raise PlanningTimeoutError("候选规划达到时间限制，尚未开始求解")
    solver.parameters.max_time_in_seconds = remaining
    status = solver.solve(model)
    if status == cp_model.INFEASIBLE:
        raise PlanningInfeasibleError(_infeasible_message(blocks, mode, candidates))
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise PlanningTimeoutError(
            f"规划尚未取得可行结果（{solver.status_name(status)}），不是已证明无解"
        )
    selected = tuple(c for i, c in enumerate(candidates) if solver.boolean_value(chosen[i]))
    return Partition(
        selected, solver.status_name(status), int((monotonic() - started) * 1000), len(candidates)
    )


def _infeasible_message(
    blocks: tuple[ContentBlock, ...], mode: VideoMode, candidates: tuple[Candidate, ...]
) -> str:
    # Explain the first blocked reachable boundary, without changing the plan.
    reachable = {0}
    outgoing: dict[int, list[int]] = {}
    for candidate in candidates:
        outgoing.setdefault(candidate.start, []).append(candidate.end)
    for boundary in range(len(blocks)):
        if boundary in reachable:
            reachable.update(outgoing.get(boundary, ()))
    blocked = max(reachable)
    block = blocks[blocked]
    rule = mode.durations
    minimum = rule.values_ms[0] if rule.kind == "discrete" else rule.minimum_ms
    maximum = rule.values_ms[-1] if rule.kind == "discrete" else rule.maximum_ms
    assert minimum is not None and maximum is not None
    prefix = f"视频规格不兼容：第 {blocked + 1} 个表演镜头（{block.key}）。"
    if block.duration("estimated_ms") > maximum:
        reason = (
            f"完整表演预计 {block.duration('estimated_ms') / 1000:g} 秒，"
            f"超过模式单段上限 {maximum / 1000:g} 秒。"
        )
    elif mode.max_shots == 1 and block.duration("maximum_ms") < minimum:
        reason = (
            f"自然表演最长 {block.duration('maximum_ms') / 1000:g} 秒，"
            f"但模式最短 {minimum / 1000:g} 秒且每段仅允许 1 个镜头，不能强行拉长。"
        )
    else:
        reason = (
            f"当前模式单段范围 {minimum / 1000:g}–{maximum / 1000:g} 秒、"
            f"最多 {mode.max_shots} 个镜头；完整表演、安全切点或参考素材组合无法匹配。"
        )
    return prefix + reason + "已保留模型响应；请核对模式规格或选择兼容模式，不会截短对白、丢图或重新生成视频。"
