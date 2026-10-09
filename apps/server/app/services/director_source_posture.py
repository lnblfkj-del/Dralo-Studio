"""A narrow, cited check for explicit named postures, not general semantic NLP."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.core.errors import ValidationError

RULE_VERSION = 1
RULE_KEY = "source_posture_rule_version"
CONTRACT_KEY = "source_posture_contract"
LOCKS_KEY = "source_posture_locked_shot_ids"

_POSES = {
    "站起身": ("standing", True), "站起来": ("standing", True),
    "站起": ("standing", True), "起身": ("standing", True), "起立": ("standing", True),
    "坐下来": ("sitting", True), "坐下": ("sitting", True),
    "蹲下来": ("crouching", True), "蹲下": ("crouching", True),
    "躺下来": ("lying", True), "躺下": ("lying", True),
    "站在": ("standing", False), "站着": ("standing", False), "站立": ("standing", False),
    "站姿": ("standing", False),
    "相对而坐": ("sitting", False), "对坐": ("sitting", False), "坐在": ("sitting", False),
    "坐着": ("sitting", False), "坐于": ("sitting", False), "坐姿": ("sitting", False),
    "蹲在": ("crouching", False), "蹲着": ("crouching", False), "蹲姿": ("crouching", False),
    "躺在": ("lying", False), "躺着": ("lying", False), "躺姿": ("lying", False),
}
_POSE_WORD = "|".join(map(re.escape, sorted(_POSES, key=len, reverse=True)))
_POSE = re.compile(
    r"^(?:(?:仍然|仍|一直|继续|保持|分别|各自|隔桌|相对|面对面|已经|已|正|均|都|还|先|再|随后|然后|又))*"
    rf"(?P<pose>{_POSE_WORD})"
)
_ANY_POSE = re.compile(_POSE_WORD)
_UNCERTAIN = re.compile(r'["“”‘’]|如果|假如|仿佛|可能|似乎|想象|回忆|曾经|以前|照片|画像|写着|描述|并非|并未|不是|没有|未|不|别|勿|禁止')
_PRESERVE = re.compile(
    r"^(?:仍|继续)?保持(?:在)?(?:桌子两侧(?:的)?(?:位置|站位)|原位|原有(?:位置|站位)|既有(?:位置|站位)|站位)"
)
_LABELS = {"standing": "站姿", "sitting": "坐姿", "crouching": "蹲姿", "lying": "躺姿"}
_PRONOUN = re.compile(r"^(?:他们|她们|两人|二人|众人|他|她)(?:仍|继续|随即|随后|已|正)?(?:走|离开|挪|移动|转身)")


def _actors(execution: dict[str, Any]):
    candidates: dict[str, set[tuple[int, str]]] = {}
    for index, row in enumerate(execution["input"].get("asset_catalog") or []):
        if row.get("asset_type") != "character" or not row.get("asset_name"):
            continue
        canonical = str(row["asset_name"]).strip()
        for value in [canonical, *(row.get("aliases") or [])]:
            name = str(value).strip()
            if name:
                candidates.setdefault(name, set()).add((index, canonical))
    names = {name: next(iter(owners))[1] for name, owners in candidates.items() if len(owners) == 1}
    if not names:
        return names, None
    alternatives = []
    for name in sorted(names, key=len, reverse=True):
        boundary = r"(?![A-Za-z0-9_])" if name[-1].isascii() and name[-1].isalnum() else ""
        alternatives.append(re.escape(name) + boundary)
    actor = "(?:" + "|".join(alternatives) + ")"
    return names, (re.compile(rf"^(?P<names>{actor}(?:[、与和及]{actor})*)(?P<rest>.*)$"),
                   re.compile(actor))


def _events(text: str, names, pattern):
    if pattern is None:
        return []
    subject_pattern, name_pattern = pattern
    # A quotation may span clauses/lines. Do not turn quoted dialogue into a fact.
    if re.search(r'["“”‘’]', text):
        return [{"actors": [], "unknown": True}]
    result = []
    # Only carry an explicit subject across commas, never across sentences.
    for sentence in re.split(r"[。；;\n]", text):
        carried: list[str] = []
        for clause in re.split(r"[，,]", sentence):
            clause = clause.strip()
            match = subject_pattern.match(clause)
            subjects = (
                list(dict.fromkeys(names[item[0]] for item in name_pattern.finditer(match["names"])))
                if match else carried
            )
            rest = match["rest"] if match else clause
            if match:
                carried = subjects
            uncertain = bool(_UNCERTAIN.search(clause))
            pose = _POSE.match(rest) if not uncertain else None
            if pose and _ANY_POSE.search(rest[pose.end():]):
                pose = None
            if pose and subjects:
                value, transition = _POSES[pose["pose"]]
                result.append({"actors": subjects, "pose": value, "transition": transition,
                               "quote": clause[:len(clause) - len(rest) + pose.end()]})
            elif subjects and _PRESERVE.match(rest) and not uncertain:
                result.append({"actors": subjects, "preserve": True})
            elif match or _ANY_POSE.search(clause) or _PRONOUN.match(clause):
                # Unknown actions, pronouns, quotations and conditions are not
                # evidence that an earlier posture definitely persists.
                result.append({"actors": subjects if match else [], "unknown": True})
                if uncertain:
                    carried = []
    return result


def build_contract(execution: dict[str, Any], outline: dict[str, Any], segment: dict[str, Any]):
    from app.services.director_response_assembly import protected_source_lines

    names, pattern = _actors(execution)
    snapshot = execution["input"]
    rows = {row["line"]: row for row in snapshot.get("source_lines") or []}
    protected = {line for row in protected_source_lines(snapshot).values() for line in row["source_lines"]}
    targets = set(segment["shot_ids"])
    locks = set(execution.get(LOCKS_KEY) or [])
    contract: dict[str, Any] = {"version": RULE_VERSION, "scope": "explicit_named_posture_only",
                              "entry": [], "exit": [], "shots": []}
    target_scene = next(shot["scene_id"] for shot in outline["shots"] if shot["shot_id"] in targets)
    if any(shot["shot_id"] in locks and shot["scene_id"] == target_scene for shot in outline["shots"]):
        return {**contract, "unverified_reason": "locked_scene"}
    facts: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()
    scene_id = None
    started = False
    for shot in outline["shots"]:
        if shot["scene_id"] != scene_id:
            facts, seen = {}, set()
            scene_id = shot["scene_id"]
        current = shot["shot_id"] in targets
        if current and not started:
            entry = deepcopy(facts)
            started = True
        allowed = {name: {fact["pose"]: deepcopy(fact)} for name, fact in facts.items()}
        unknown: set[str] = set()
        changed: set[str] = set()
        narrative = [line for line in sorted(shot.get("source_lines") or []) if line not in protected]
        for line in narrative:
            if line in seen:
                continue
            seen.add(line)
            for event in _events(rows[line]["text"], names, pattern):
                actors = event["actors"]
                if event.get("unknown"):
                    affected = actors or list(facts)
                    for actor in affected:
                        facts.pop(actor, None)
                        unknown.add(actor)
                        changed.add(actor)
                elif event.get("pose"):
                    for actor in actors:
                        fact = {"actor": actor, "pose": event["pose"], "line": line, "quote": event["quote"]}
                        facts[actor] = fact
                        allowed.setdefault(actor, {})[fact["pose"]] = deepcopy(fact)
                        unknown.discard(actor)
                        if (current and shot["shot_id"] == segment["shot_ids"][0]
                                and actor not in changed and not event["transition"]):
                            entry[actor] = deepcopy(fact)
                        changed.add(actor)
        if current:
            contract["shots"].append({"shot_id": shot["shot_id"], "allowed": [
                {"actor": actor, "evidence": list(poses.values())}
                for actor, poses in sorted(allowed.items()) if actor not in unknown
            ]})
            if shot["shot_id"] == segment["shot_ids"][-1]:
                contract["entry"] = list(entry.values())
                contract["exit"] = list(deepcopy(facts).values())
                break
    return contract


def validate_output(payload: dict[str, Any], output) -> None:
    execution = payload["director_execution"]
    version = execution.get(RULE_KEY)
    if version is None:
        if CONTRACT_KEY in execution:
            raise ValidationError("姿态来源检查缺少冻结规则版本")
        return
    if type(version) is not int or version != RULE_VERSION:
        raise ValidationError("姿态来源检查版本不支持，不能降级处理")
    pipeline = payload["director_pipeline"]
    contract = build_contract(execution, pipeline["outline"], pipeline["segment_outline"])
    if contract != execution.get(CONTRACT_KEY):
        raise ValidationError("姿态来源检查与冻结输入不一致，不能恢复旧结果")
    names, pattern = _actors(execution)
    by_shot = {item["shot_id"]: item for item in contract["shots"]}
    checks = []
    for shot in output.shots:
        constraints = {row["actor"]: row["evidence"] for row in by_shot.get(shot.shot_id, {}).get("allowed", [])}
        checks.extend((f"镜头 {shot.shot_id} {label}", getattr(shot, field), constraints)
                      for field, label in (("subject", "主体"), ("action", "动作")))
    if output.segments:
        segment = output.segments[0]
        checks.extend((label, getattr(segment, field), {row["actor"]: [row] for row in contract[key]})
                      for field, label, key in (("entry_state", "进入状态", "entry"), ("exit_state", "结束状态", "exit")))
    for field, text, constraints in checks:
        for event in _events(str(text or ""), names, pattern):
            if not event.get("pose"):
                continue
            for actor in event["actors"]:
                evidence = constraints.get(actor) or []
                if evidence and event["pose"] not in {row["pose"] for row in evidence}:
                    source = evidence[-1]
                    raise ValidationError(
                        f"{field}把{actor}写成{_LABELS[event['pose']]}，与原文第 {source['line']} 行的明确姿态冲突；已保留响应，不会自动改写",
                        details={"failure_kind": "source_posture_conflict", "actor": actor, "field": field,
                                 "generated_posture": event["pose"], "source_evidence": evidence},
                    )
