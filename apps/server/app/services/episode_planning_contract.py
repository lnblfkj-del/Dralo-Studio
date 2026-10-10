"""Pure contract checks for R0; not an alternate generation or legacy route."""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from app.schemas.episode_planning import (
    Contract,
    FrozenEpisodePlan,
    PlanningTask,
    SegmentDetail,
    VideoCapability,
)


def fingerprint(value: Contract) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_model_response(text: str, schema: type[Contract], label: str):
    """Accept one JSON document or its complete fence, never repair the contract."""
    from app.services.structured_output_service import parse_json_document

    document, _ = parse_json_document(text, label)
    return schema.model_validate_json(json.dumps(document, ensure_ascii=False, allow_nan=False))


@dataclass(frozen=True)
class CompatibilityIssue:
    segment_key: str
    code: str


def compatibility_issues(
    plan: FrozenEpisodePlan,
    target: VideoCapability,
) -> tuple[CompatibilityIssue, ...]:
    """No calls, inference, truncation or plan mutations; caller selects the route."""
    issues = []
    modes = {mode.key: mode for mode in target.modes}
    original_modes = {mode.key: mode for mode in plan.capability.modes}
    for segment in plan.segments:

        def reject(code, key=segment.key):
            issues.append(CompatibilityIssue(key, code))

        mode = modes.get(segment.mode_key)
        original = original_modes.get(segment.mode_key)
        if mode is None or original is None:
            reject("mode_unavailable")
            continue
        if (mode.input_mode, mode.aspect_ratio, mode.resolution) != (
            original.input_mode,
            original.aspect_ratio,
            original.resolution,
        ):
            reject("mode_parameters_changed")
        if not mode.durations.allows(segment.requested_duration_ms):
            reject("duration_requires_replan")
        if len(segment.shots) > mode.max_shots:
            reject("shot_count_requires_replan")
        counts = Counter(ref.role for ref in segment.references)
        allowed = {limit.role: limit for limit in mode.reference_limits}
        if (
            set(counts) - set(allowed)
            or any(
                not limit.minimum <= counts[role] <= limit.maximum
                for role, limit in allowed.items()
            )
            or len(segment.references) > mode.max_total_references
        ):
            reject("reference_constraints")
        if segment.requires_native_dialogue and not mode.native_dialogue:
            reject("native_dialogue_unavailable")
        if segment.requires_native_audio and not mode.native_audio:
            reject("native_audio_unavailable")
        if segment.background_music and mode.bgm_control == "unsupported":
            reject("background_music_unavailable")
    return tuple(issues)


def validate_frozen_plan(plan: FrozenEpisodePlan) -> None:
    issues = compatibility_issues(plan, plan.capability)
    if issues:
        raise ValueError("Invalid frozen plan: " + ",".join(i.code for i in issues))


def validate_detail(plan: FrozenEpisodePlan, detail: SegmentDetail) -> None:
    if detail.plan_fingerprint != fingerprint(plan):
        raise ValueError("Response belongs to a different plan")
    segment = next((s for s in plan.segments if s.key == detail.segment_key), None)
    if segment is None or tuple(shot.key for shot in detail.shots) != tuple(
        shot.key for shot in segment.shots
    ):
        raise ValueError("Response must fill exactly the frozen segment shots")


def assert_submission_ready(
    plan: FrozenEpisodePlan,
    current: VideoCapability,
    execution_epoch: str,
) -> None:
    validate_frozen_plan(plan)
    if execution_epoch != plan.execution_epoch:
        raise ValueError("Execution epoch changed; stale task cannot submit")
    if fingerprint(current) != fingerprint(plan.capability):
        raise ValueError("Capability changed; recheck and freeze a new plan")
    if current.verification != "channel_verified":
        raise ValueError("Selected channel capability has not been verified")


def recovery_action(task: PlanningTask) -> str:
    if task.state == "save_failed":
        return "retry_save"
    if task.state in {"response_received", "processing_failed"}:
        return "reprocess_response"
    if task.state in {"submitted", "result_unknown"}:
        return "query_remote" if task.remote_task_id else "reconcile_submission"
    if task.state == "provider_failed":
        return "confirm_paid_retry"
    return "none"
