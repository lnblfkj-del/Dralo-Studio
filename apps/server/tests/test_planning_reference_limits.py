"""Public, provider-free regressions for script planning and image limits."""

import json
from types import SimpleNamespace

import pytest

from app.core.errors import ConflictError
from app.schemas.episode_planning import SourceUnit, VideoCapability
from app.schemas.episode_timing import ContentAnalysis, ContentBlock, PlanningSources, TimingEvent
from app.services.episode_planning_contract import fingerprint
from app.services.episode_planning_coordinator import create_plan
from app.services.episode_planning_solver import PlanningInfeasibleError
from app.services.episode_planning_storage import new_plan_record, read_plan
from app.services.image_model_contract import validate_image_inputs


def fixture(durations):
    units = tuple(SourceUnit(key=f"source-{i}", kind="action", text=f"Action {i}",
                             scene_key="room") for i in range(len(durations)))
    sources = PlanningSources(script_fingerprint="b" * 64, units=units)
    blocks = tuple(ContentBlock(
        key=f"shot-{i}", scene_key="room", source_keys=(unit.key,),
        events=(TimingEvent(source_key=unit.key, start_ms=0, minimum_ms=minimum,
                            estimated_ms=estimate, maximum_ms=maximum, basis="Test estimate"),),
        boundary_after=True, boundary_basis="Complete action",
    ) for i, (unit, (minimum, estimate, maximum)) in enumerate(zip(units, durations, strict=True)))
    analysis = ContentAnalysis(contract_version="episode-timing.v1",
                               source_fingerprint=fingerprint(sources), blocks=blocks)
    capability = VideoCapability.model_validate_json(json.dumps({
        "provider_model_id": 1, "model_id": "test-video", "protocol": "test-only",
        "endpoint_fingerprint": "a" * 64, "verification": "mock_verified",
        "evidence": ["provider-free-test"],
        "modes": [{"key": "test-mode", "input_mode": "text", "aspect_ratio": "16:9",
                   "resolution": "720p", "durations": {"kind": "discrete", "values_ms": [4000, 15000]},
                   "max_shots": 5, "reference_limits": [], "max_total_references": 0,
                   "native_audio": True, "native_dialogue": True, "bgm_control": "prompt_preference"}],
    }))
    return sources, analysis, capability


def plan(durations, budget=None):
    sources, analysis, capability = fixture(durations)
    result = create_plan(
        sources=sources, analysis=analysis, capability=capability, mode_key="test-mode",
        episode_id=1, script_revision=1, asset_fingerprint="c" * 64, execution_epoch="public-test",
        plan_revision=1, compiler_version="test", editorial_target_ms=None,
        background_music=False, reference_budget=budget,
    )
    assert result.plan.sources == sources.units
    assert read_plan(new_plan_record(result.plan, workspace_id=None, analysis=analysis)) == result.plan
    return result


def test_extra_reference_images_split_into_more_segments():
    result = plan([(4000, 4000, 4000)] * 2, lambda blocks, *_: len(blocks) == 1)
    assert len(result.plan.segments) == 2


def test_one_overloaded_shot_does_not_disable_other_reference_limits():
    def budget(blocks, *_):
        return all(block.key != "shot-0" for block in blocks) and len(blocks) == 1
    result = plan([(4000, 4000, 4000)] * 3, budget)
    assert len(result.plan.segments) == 3
    assert [candidate.reference_overflow for candidate in result.solves[0].candidates] == [True, False, False]


def test_timing_estimate_can_fit_without_altering_original_content():
    result = plan([(13600, 15500, 18000)])
    assert result.plan.segments[0].requested_duration_ms == 15000
    assert result.warnings
    with pytest.raises(PlanningInfeasibleError):
        plan([(16000, 17000, 18000)])


def test_image_limit_is_configured_independently_and_not_overridden_by_request():
    model = SimpleNamespace(model_id="test-image", capabilities=["reference_images"],
                            default_params={"max_reference_images": 10})
    validate_image_inputs(model, {}, list(range(10)))
    with pytest.raises(ConflictError):
        validate_image_inputs(model, {"max_reference_images": 99}, list(range(11)))
    model.default_params = {}
    with pytest.raises(ConflictError, match="尚未配置"):
        validate_image_inputs(model, {}, [1])
