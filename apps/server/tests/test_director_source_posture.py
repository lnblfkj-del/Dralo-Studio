"""Cited posture checks stay narrow, immutable and compatible with old jobs."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.core.errors import ValidationError
from app.services.director_source_posture import (
    CONTRACT_KEY, LOCKS_KEY, RULE_KEY, RULE_VERSION, build_contract, validate_output,
)
from app.services.episode_director_pipeline_service import _segment_execution, _segment_prompt


def fixture(lines=None, names=None):
    names = names or [("秦舒", []), ("魏衡", [])]
    lines = lines or ["秦舒将检查清单放在桌子中央；魏衡站在桌子另一侧。",
                      "秦舒与魏衡保持桌子两侧的位置。"]
    execution = {RULE_KEY: RULE_VERSION, LOCKS_KEY: [], "skill_bundle": [],
                 "video_model_capability_snapshot": {"durations": [5], "max_shots_per_segment": 2},
                 "input": {"shots": [], "source_lines": [{"line": i, "text": text} for i, text in enumerate(lines, 1)],
                           "asset_catalog": [{"asset_type": "character", "asset_id": i,
                                              "asset_name": name, "aliases": aliases}
                                             for i, (name, aliases) in enumerate(names, 1)]}}
    outline = {"scenes": [{"scene_id": 1, "name": "维修舱"}],
               "shots": [{"shot_id": i, "scene_id": 1, "source_lines": [i], "duration": 5, "asset_ids": []}
                         for i in range(1, len(lines) + 1)],
               "segments": [{"key": f"s{i}", "title": f"片段{i}", "shot_ids": [i], "generation_duration": 5}
                            for i in range(1, len(lines) + 1)]}
    return execution, outline


def payload_for(execution, outline, index=-1):
    execution = deepcopy(execution)
    part = outline["segments"][index]
    execution[CONTRACT_KEY] = build_contract(execution, outline, part)
    return {"director_execution": execution, "director_pipeline": {"outline": outline, "segment_outline": part}}


def output_for(payload, *, subject="魏衡站在桌边。", action="魏衡保持站位。", entry="", exit=""):
    part = payload["director_pipeline"]["segment_outline"]
    return SimpleNamespace(shots=[SimpleNamespace(shot_id=part["shot_ids"][0], subject=subject, action=action)],
                           segments=[SimpleNamespace(entry_state=entry, exit_state=exit)])


@pytest.mark.parametrize("field", ["subject", "action", "entry", "exit"])
def test_joint_sitting_contradiction_is_rejected_with_exact_prior_source(field):
    execution, outline = fixture()
    payload = payload_for(execution, outline)
    output = output_for(payload, **{field: "秦舒与魏衡隔桌对坐于维修舱。"})
    before = deepcopy((payload, vars(output.shots[0]), vars(output.segments[0])))
    with pytest.raises(ValidationError) as error:
        validate_output(payload, output)
    assert error.value.details["failure_kind"] == "source_posture_conflict"
    assert error.value.details["actor"] == "魏衡"
    assert error.value.details["source_evidence"][0] == {
        "actor": "魏衡", "pose": "standing", "line": 1, "quote": "魏衡站在"}
    assert before == (payload, vars(output.shots[0]), vars(output.segments[0]))


@pytest.mark.parametrize("pose,expected", [("站在", "standing"), ("坐在", "sitting"),
                                          ("蹲在", "crouching"), ("躺在", "lying")])
def test_explicit_pose_and_cited_alias_are_recognized(pose, expected):
    execution, outline = fixture([f"老魏{pose}桌边。"], [("魏衡", ["老魏"])])
    payload = payload_for(execution, outline)
    assert payload["director_execution"][CONTRACT_KEY]["exit"] == [
        {"actor": "魏衡", "pose": expected, "line": 1, "quote": f"老魏{pose}"}]
    validate_output(payload, output_for(payload, subject=f"魏衡{pose}桌边。", action="维持原位。"))


@pytest.mark.parametrize("name", ["王和", "何与", "罗及", "Ann", "王和与何与"])
def test_names_containing_joiners_are_not_split(name):
    execution, outline = fixture([f"{name}站在桌边。"], [(name, [])])
    payload = payload_for(execution, outline)
    assert payload["director_execution"][CONTRACT_KEY]["exit"][0]["actor"] == name
    with pytest.raises(ValidationError):
        validate_output(payload, output_for(payload, subject=f"{name}坐在桌边。"))


@pytest.mark.parametrize("names,text", [
    ([("甲", ["小王"]), ("乙", ["小王"])], "小王站在桌边。"),
    ([("甲", []), ("甲", [])], "甲站在桌边。"),
    ([("Ann", [])], "Anna站在桌边。"),
])
def test_ambiguous_or_partial_name_is_not_an_actor_fact(names, text):
    execution, outline = fixture([text], names)
    assert build_contract(execution, outline, outline["segments"][0])["exit"] == []


@pytest.mark.parametrize("line", ["魏衡没有站起来。", "如果魏衡坐下，秦舒就走。",
                                  "魏衡可能坐下。", "魏衡回忆曾经坐在这里。",
                                  '纸条写着“魏衡坐在桌边。魏衡躺在地上。”',
                                  "魏衡：我坐在桌边。", "环境声：魏衡坐下的声音。"])
def test_reported_conditional_negated_and_protected_lines_are_not_pose_facts(line):
    execution, outline = fixture([line])
    assert build_contract(execution, outline, outline["segments"][0])["exit"] == []


def test_folded_quoted_speech_is_excluded_as_a_whole():
    execution, outline = fixture(['魏衡：“我说', '魏衡坐在桌边。”'])
    assert build_contract(execution, outline, outline["segments"][1])["exit"] == []


def test_source_preservation_is_not_invalidated_by_knowledge_statement():
    execution, outline = fixture([
        "内景 维修舱 日。秦舒将检查清单放在桌子中央；魏衡站在桌子另一侧。两人尚未确认未勾项目。",
        "内景 维修舱 日。检查清单仍在桌子中央；秦舒与魏衡保持桌子两侧的位置。两人尚未确认未勾项目。"])
    payload = payload_for(execution, outline)
    with pytest.raises(ValidationError):
        validate_output(payload, output_for(payload, subject="秦舒与魏衡隔桌对坐于维修舱。"))


def test_multiple_unparsed_postures_in_one_clause_are_not_claimed_as_single_fact():
    execution, outline = fixture(["魏衡站在门边但又坐在桌边。"])
    assert build_contract(execution, outline, outline["segments"][0])["exit"] == []


@pytest.mark.parametrize("prefix", ["内景 维修舱 日。", "桌上检查清单仍在。"])
def test_initial_explicit_pose_after_header_or_other_subject_is_not_a_transition(prefix):
    execution, outline = fixture(["魏衡站在桌边。", prefix, "魏衡坐在桌边。"])
    outline["shots"][1]["source_lines"] = [2, 3]
    outline["shots"] = outline["shots"][:2]
    outline["segments"] = outline["segments"][:2]
    payload = payload_for(execution, outline)
    assert payload["director_execution"][CONTRACT_KEY]["entry"][0]["pose"] == "sitting"
    validate_output(payload, output_for(payload, subject="魏衡", action="魏衡坐在桌边。",
                                       entry="魏衡坐在桌边。", exit="魏衡坐在桌边。"))


@pytest.mark.parametrize("line", ["魏衡挪到门边。", "他走出舱门。", "两人转身离开。",
                                  "魏衡可能坐下。", "他坐下。"])
def test_ambiguous_new_action_invalidates_old_fact_without_guessing(line):
    execution, outline = fixture(["魏衡站在桌边。", line])
    payload = payload_for(execution, outline)
    assert payload["director_execution"][CONTRACT_KEY]["exit"] == []
    assert payload["director_execution"][CONTRACT_KEY]["shots"][0]["allowed"] == []
    validate_output(payload, output_for(payload, subject="魏衡坐在门边。"))


@pytest.mark.parametrize("first,next_line,entry,action,exit", [
    ("魏衡站在桌边。", "魏衡坐下。", "魏衡站在桌边。", "魏衡站在桌边，随后坐下。", "魏衡坐在桌边。"),
    ("魏衡蹲在工作台前。", "魏衡将左手移开，站起身。", "魏衡蹲在台前。", "魏衡蹲在台前，起身。", "魏衡站在台前。"),
    ("魏衡坐在桌边。", "魏衡站起身。", "魏衡坐在桌边。", "魏衡坐在桌边，站起身。", "魏衡站在桌边。"),
])
def test_source_authorized_transitions_allow_before_and_after_poses(first, next_line, entry, action, exit):
    execution, outline = fixture([first, next_line])
    payload = payload_for(execution, outline)
    validate_output(payload, output_for(payload, subject="魏衡", action=action, entry=entry, exit=exit))
    with pytest.raises(ValidationError):
        validate_output(payload, output_for(payload, subject="魏衡", action=action, entry=exit, exit=entry))


def test_same_scene_history_is_read_but_future_source_and_other_scenes_are_not():
    execution, outline = fixture(["魏衡站在桌边。", "桌面清单未变。", "魏衡坐下。"])
    payload = payload_for(execution, outline, 1)
    with pytest.raises(ValidationError):
        validate_output(payload, output_for(payload, subject="魏衡坐在桌边。"))
    outline["shots"][1]["scene_id"] = 2
    assert build_contract(execution, outline, outline["segments"][1])["exit"] == []


def test_shared_source_row_is_not_reapplied_after_a_transition():
    execution, outline = fixture(["魏衡站在桌边。", "魏衡坐下。", "桌上清单仍在。"])
    outline["shots"][2]["source_lines"] = [1, 3]
    contract = build_contract(execution, outline, outline["segments"][2])
    assert contract["exit"][0]["pose"] == "sitting"


def test_locked_scene_is_explicitly_unverified_and_does_not_replace_approved_edits():
    execution, outline = fixture()
    execution[LOCKS_KEY] = [1]
    payload = payload_for(execution, outline)
    assert payload["director_execution"][CONTRACT_KEY]["unverified_reason"] == "locked_scene"
    validate_output(payload, output_for(payload, subject="魏衡坐在桌边。"))


@pytest.mark.parametrize("version", [True, False, 0, 2, "1", None])
def test_invalid_or_missing_version_cannot_silently_downgrade(version):
    execution, outline = fixture()
    payload = payload_for(execution, outline)
    payload["director_execution"][RULE_KEY] = version
    with pytest.raises(ValidationError):
        validate_output(payload, output_for(payload))


def test_contract_tampering_or_missing_contract_cannot_pass():
    execution, outline = fixture()
    payload = payload_for(execution, outline)
    for value in (None, {"version": 1, "exit": []}):
        payload["director_execution"][CONTRACT_KEY] = value
        with pytest.raises(ValidationError):
            validate_output(payload, output_for(payload))


def test_old_jobs_do_not_acquire_new_rules_and_scoped_new_prompt_is_immutable():
    execution, outline = fixture()
    before = deepcopy((execution, outline))
    parent = SimpleNamespace(payload={"director_execution": execution})
    scoped = _segment_execution(parent, outline, outline["segments"][1])
    assert scoped[CONTRACT_KEY]["exit"][0]["line"] == 1
    assert "只读姿态来源证据" in _segment_prompt(parent, outline, outline["segments"][1])
    assert before == (execution, outline)
    execution.pop(RULE_KEY)
    execution.pop(LOCKS_KEY)
    scoped = _segment_execution(parent, outline, outline["segments"][1])
    assert CONTRACT_KEY not in scoped
    assert "只读姿态来源证据" not in _segment_prompt(parent, outline, outline["segments"][1])
    payload = {"director_execution": scoped}
    validate_output(payload, output_for({"director_pipeline": {"segment_outline": outline["segments"][1]}},
                                       subject="魏衡坐在桌边。"))


@pytest.mark.asyncio
async def test_real_finalizer_checks_assembled_creative_content_without_rewriting_response(monkeypatch):
    from app.schemas.director_response_protocol import response_contract
    from app.services.episode_director_pipeline_service import finalize_segment
    execution, outline = fixture()
    parent = SimpleNamespace(payload={"director_execution": execution})
    part = outline["segments"][1]
    payload = {"director_execution": _segment_execution(parent, outline, part),
               "response_protocol": response_contract("segment"),
               "director_pipeline": {"outline": outline, "segment_outline": part}}
    wire = {"protocol_version": "director.segment.v2", "segment_key": part["key"],
            "entry_state": "魏衡站在桌边。", "exit_state": "魏衡站在桌边。", "negative_prompt": "",
            "continuity_issues": [], "shots": [{"shot_id": 2, "shot_size": "中景", "camera_angle": "平视",
            "camera_movement": "固定", "action": "魏衡保持站位。", "subject": "秦舒与魏衡隔桌对坐。",
            "expression": "谨慎", "dialogue_tone": "自然"}]}
    response = {"text": json.dumps(wire, ensure_ascii=False), "finish_reason": "stop"}
    before = deepcopy((payload, response))
    def unexpected(*args):
        raise AssertionError("A contradictory proposal must not reach draft/business acceptance")
    monkeypatch.setattr("app.services.episode_director_validation.validate_model_result", unexpected)
    with pytest.raises(ValidationError) as error:
        await finalize_segment(SimpleNamespace(payload=payload), response)
    assert error.value.details["failure_kind"] == "source_posture_conflict"
    assert before == (payload, response)
