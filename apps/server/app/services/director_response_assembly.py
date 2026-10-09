"""Parse a small wire response and assemble only from frozen director inputs."""

import json
import re
from collections import Counter
from copy import deepcopy
from hashlib import sha256

from pydantic import ValidationError as SchemaError

from app.core.errors import ValidationError
from app.schemas.director_response_protocol import (
    ASSEMBLY_RULE_VERSION, OutlineWireOutput, SegmentWireOutput, response_contract,
)
from app.schemas.episode_director import DirectorModelOutput, DirectorShotProposal
from app.services import text_model_policy_service
from app.services.segment_script_semantics import build_structured_script, compile_prompt, quoted_dialogue_closed


def _role_names(snapshot):
    names = set()
    for row in snapshot.get("asset_catalog") or []:
        if row.get("asset_type") == "character":
            names.update(str(value).strip().casefold() for value in [row.get("asset_name"), *(row.get("aliases") or [])] if value)
    for shot in snapshot.get("shots") or []:
        for line in str(shot.get("dialogue") or "").splitlines():
            match = re.match(r"^([^：:\n]{1,30})[：:]", line)
            if match:
                names.add(match.group(1).strip().casefold())
    return names


def _source_kind(text, names):
    from app.services.episode_auto_planning_validation import protected_source_kind
    kind = protected_source_kind(text)
    match = re.match(r"^([^：:\n]{1,30})[：:]\s*(.*)$", text)
    if not kind and match:
        label = match.group(1).strip()
        # An explicit narration cue identifies spoken text, not an invented
        # character or ambience. Preserve its label and exact source sentence.
        if label == "旁白":
            return "dialogue"
        base = re.sub(r"\s*[（(](?:OS|O\.S\.|VO|V\.O\.|画外音|内心|旁白)[）)]\s*$", "", label, flags=re.I).strip().casefold()
        if base in names:
            kind = "dialogue"
    return kind


def protected_source_lines(snapshot):
    # Unquoted speech needs a frozen role name. Unknown colon lines are not
    # silently treated as action or bound to an invented speaker.
    names = _role_names(snapshot)
    rows = list(snapshot.get("source_lines") or [])
    protected = {}
    index = 0
    while index < len(rows):
        row = rows[index]
        kind = _source_kind(row["text"], names)
        index += 1
        if not kind:
            prefix = re.match(r"^([^：:\n]{1,30})[：:]", row["text"])
            if prefix and not re.fullmatch(
                r"(?:#+\s*)?(?:(?:第[\d一二三四五六七八九十百]+集)|"
                r"(?:场景|地点|时间|人物|角色|标题|集数|时长|剧情|动作|分镜|镜头|备注|说明|注)[\s\d一二三四五六七八九十_-]*)",
                prefix.group(1).strip(),
            ):
                raise ValidationError(f"正文第 {row['line']} 行的说话人或说明不明确，请先核对角色或标明引号台词，不会省略该行")
            continue
        group = [row]
        if kind == "dialogue":
            cue = re.fullmatch(r"[^：:\n]{1,30}[：:]\s*", row["text"])
            quoted_row = row
            if cue:
                if index >= len(rows) or not rows[index]["text"].lstrip().startswith(('"', '“')):
                    raise ValidationError("角色提示后缺少明确的带引号台词，请核对原文，不会猜测说话内容")
                quoted_row = rows[index]
                group.append(quoted_row)
                index += 1
            match = re.match(r'^[^：:\n]{1,30}[：:]\s*(["“])(.*)', row["text"])
            if match or cue:
                quoted = quoted_row["text"].lstrip() if cue else match.group(1) + match.group(2)
                while not quoted_dialogue_closed(quoted):
                    if index >= len(rows) or _source_kind(rows[index]["text"], names):
                        raise ValidationError("正文带引号的台词未闭合，请核对原文后规划，不会猜测或省略后半句")
                    group.append(rows[index])
                    quoted += "\n" + rows[index]["text"]
                    index += 1
        protected[row["line"]] = {**row, "field": kind, "source_lines": [part["line"] for part in group],
                                  "text": "\n".join(part["text"] for part in group)}
    return protected


def _line_occurrences(value, text):
    lines = [part.strip() for part in value.splitlines()]
    expected = [part.strip() for part in text.splitlines()]
    return sum(lines[index:index + len(expected)] == expected for index in range(len(lines)))


def _owned_occurrences(value, patterns):
    lines = [part.strip() for part in value.splitlines()]
    counts = Counter()
    for index, line in enumerate(lines):
        for text, expected in patterns.get(line, []):
            if lines[index:index + len(expected)] == expected:
                counts[text] += 1
    return counts


def _duplicate_safe_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate field: {key}")
        result[key] = value
    return result


def _same_json(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=True, allow_nan=False)


def _parse_native_repetition(value, decoder, conversions):
    raw, end = decoder.raw_decode(value)
    repeats = 1
    while value[end:].strip():
        if repeats >= 3:
            raise ValueError("Too many repeated response objects")
        offset = end + len(value[end:]) - len(value[end:].lstrip())
        repeated, end = decoder.raw_decode(value, offset)
        if not isinstance(raw, dict) or not _same_json(raw, repeated):
            raise ValueError("Conflicting response objects")
        repeats += 1
    if repeats > 1:
        conversions.append(f"identical_repeated_response:{repeats}")
    return raw


def parse_response(payload, result, stage):
    contract = payload.get("response_protocol")
    expected = response_contract(stage)
    if contract != expected:
        raise ValidationError("任务响应协议不支持或规则快照已变化，不能混用协议处理")
    submission = payload.get("text_submission") or {}
    text_model_policy_service.ensure_complete_result(result, submission=submission)
    finish_reason = str(result.get("finish_reason") or submission.get("finish_reason") or "").lower()
    if result.get("refusal") or submission.get("refusal_received") or finish_reason in {
        "content_filter", "safety", "refusal", "blocked",
    }:
        raise ValidationError("模型拒绝或安全拦截了本次请求，不能作为完整脚本保存",
                              details={"failure_kind": "model_refusal"})
    if payload.get("response_transport") and finish_reason not in {"stop", "end_turn", "stop_sequence"}:
        raise ValidationError("渠道未返回可核对的正常结束状态，已保存响应但不能认作完整脚本",
                              details={"failure_kind": "unverified_finish", "finish_reason": finish_reason})
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("模型没有返回响应内容")
    value = text.strip()
    conversions = []
    if value.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", value, re.S | re.I)
        if match is None:
            raise ValidationError("模型响应代码围栏不完整，不能补造缺失内容")
        value = match.group(1).strip()
        conversions.append("json_code_fence")
    try:
        def invalid_constant(value):
            raise ValueError(f"Invalid JSON number: {value}")
        if (payload.get("response_transport") or {}).get("mode") in {"json_object", "json_schema"}:
            decoder = json.JSONDecoder(object_pairs_hook=_duplicate_safe_object, parse_constant=invalid_constant)
            raw = _parse_native_repetition(value, decoder, conversions)
        else:
            raw = json.loads(value, object_pairs_hook=_duplicate_safe_object, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ValidationError("模型未返回完整、无重复字段的 JSON 对象",
                              details={"failure_kind": "invalid_or_truncated_json"}) from exc
    if not isinstance(raw, dict):
        raise ValidationError("模型响应必须是单个 JSON 对象")
    try:
        pending = [raw]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item)
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        digest = sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValidationError("模型响应包含无效 Unicode 文本") from exc
    return raw, {"version": expected["version"], "assembly_rules": ASSEMBLY_RULE_VERSION,
                 "response_sha256": digest,
                 "conversions": conversions}


def canonicalize_source_owners(execution, outline, audit):
    protected = protected_source_lines(execution["input"])
    owners = outline["protected_line_owners"]
    by_line = {row["line"]: row["shot_id"] for row in owners}
    if len(by_line) != len(owners) or not set(protected) <= set(by_line):
        raise ValidationError("每句正文台词或声音说明必须有且仅有一个明确镜头归属")
    continuations = {line: head for head, row in protected.items() for line in row["source_lines"] if line != head}
    by_shot = {row["shot_id"]: row for row in outline["shots"]}
    conversions = []
    # Only a redundant continuation with the same explicit owner is equivalent.
    # Never infer a missing head, change an owner, or discard an unknown line.
    for line in sorted(set(by_line) - set(protected)):
        head = continuations.get(line)
        shot_id = by_line[line]
        shot = by_shot.get(shot_id)
        if (head is None or by_line[head] != shot_id or shot is None
                or not set(protected[head]["source_lines"]) <= set(shot["source_lines"])):
            raise ValidationError("冗余正文归属与整句冻结来源冲突，不能省略或重分配")
        conversions.append(f"identical_continuation_owner:{line}->{head}@{shot_id}")
    outline["protected_line_owners"] = [row for row in owners if row["line"] in protected]
    audit["conversions"].extend(conversions)


def validate_source_owners(execution, outline, shots):
    protected = protected_source_lines(execution["input"])
    owners = outline["protected_line_owners"]
    by_line = {row["line"]: row["shot_id"] for row in owners}
    if len(by_line) != len(owners) or set(by_line) != set(protected):
        raise ValidationError("每句正文台词或声音说明必须有且仅有一个明确镜头归属")
    by_shot = {row["shot_id"]: row for row in outline["shots"]}
    for line, shot_id in by_line.items():
        if shot_id not in by_shot or not set(protected[line]["source_lines"]) <= set(by_shot[shot_id]["source_lines"]):
            raise ValidationError("台词或声音归属镜头未引用该正文行")
    positions = {shot["shot_id"]: index for index, shot in enumerate(outline["shots"])}
    ordered_owners = [positions[by_line[line]] for line in sorted(protected)]
    if ordered_owners != sorted(ordered_owners):
        raise ValidationError("台词和声音的镜头归属不能颠倒正文顺序")
    existing = {row["shot_id"]: row for row in execution["input"].get("shots") or []}
    protected_text = {field: {row["text"].strip() for row in protected.values() if row["field"] == field}
                      for field in ("dialogue", "audio_note")}
    patterns = {field: {} for field in protected_text}
    for field, values in protected_text.items():
        for text in values:
            parts = [part.strip() for part in text.splitlines()]
            patterns[field].setdefault(parts[0], []).append((text, parts))
    for shot in shots:
        source = existing.get(shot["shot_id"]) or {}
        for field in ("dialogue", "audio_note"):
            assigned = [row["text"] for line, row in protected.items()
                        if row["field"] == field and by_line[line] == shot["shot_id"]]
            value = str(source.get(field) or "")
            if source.get("is_locked") or value.strip():
                if any(not _line_occurrences(value, text) for text in assigned):
                    raise ValidationError("正文归属不能改写已有或锁定镜头的台词和声音说明")
                shot[field] = value
            else:
                shot[field] = "\n".join(assigned)
            # Match occurrences to the explicit owner table, including identical
            # repeated lines; source references alone may overlap across shots.
            expected = Counter(text.strip() for text in assigned)
            actual = _owned_occurrences(shot[field], patterns[field])
            if actual != expected:
                raise ValidationError("台词或声音说明实际归属与冻结来源归属表不一致")
    # An authored line not recognized by the existing source classifier cannot
    # be silently moved to a guessed owner when its old shot is removed.
    remaining = {row["shot_id"] for row in shots}
    for shot_id, source in existing.items():
        if shot_id in remaining:
            continue
        for field in ("dialogue", "audio_note"):
            value = str(source.get(field) or "").strip()
            if value and sum(value in str(row.get(field) or "") for row in shots) != 1:
                raise ValidationError("被替换镜头的原台词或声音无法明确转移，请保留原镜头或核对来源")
    return shots


def parse_outline(payload, result):
    raw, audit = parse_response(payload, result, "outline")
    try:
        parsed = OutlineWireOutput.model_validate(raw, strict=True)
    except SchemaError as exc:
        raise ValidationError("片段边界响应结构不完整",
                              details={"errors": exc.errors(include_input=False, include_url=False)[:10]}) from exc
    return parsed.model_dump(), audit


def _identical_alias(raw, alias, canonical, conversions):
    if alias not in raw:
        return
    if canonical not in raw or type(raw[alias]) is not type(raw[canonical]) or raw[alias] != raw[canonical]:
        raise ValidationError(f"冗余字段 {alias} 与 {canonical} 冲突，不能静默覆盖")
    raw.pop(alias)
    conversions.append(f"identical_alias:{alias}->{canonical}")


def assemble_segment(payload, result):
    raw, audit = parse_response(payload, result, "segment")
    raw = deepcopy(raw)
    _identical_alias(raw, "key", "segment_key", audit["conversions"])
    source = payload["director_execution"]["input"]["shots"]
    source_by_id = {row["shot_id"]: row for row in source}
    if isinstance(raw.get("shots"), list):
        for row in raw["shots"]:
            if not isinstance(row, dict):
                continue
            _identical_alias(row, "id", "shot_id", audit["conversions"])
            frozen = source_by_id.get(row.get("shot_id")) if type(row.get("shot_id")) is int else None
            if frozen is not None:
                for key in ("duration", "dialogue", "audio_note"):
                    if key not in row:
                        continue
                    value = frozen.get(key, "")
                    identical = (type(row[key]) in {float, int} and type(value) in {float, int}
                                 and row[key] == value) if key == "duration" else (
                                     isinstance(row[key], str) and row[key] == value)
                    if not identical:
                        raise ValidationError(f"模型返回的 {key} 与冻结来源冲突")
                    row.pop(key)
                    audit["conversions"].append(f"identical_frozen:{frozen['shot_id']}.{key}")
        if (payload.get("response_transport") or {}).get("mode") in {"json_object", "json_schema"} and len(raw["shots"]) <= 40:
            unique, seen = [], {}
            for row in raw["shots"]:
                ident = row.get("shot_id") if isinstance(row, dict) else None
                if type(ident) is int and ident in seen:
                    if not _same_json(row, seen[ident]):
                        raise ValidationError("重复镜头的创作内容冲突，不能选择其中一版或删掉空字段")
                    audit["conversions"].append(f"identical_repeated_shot:{ident}")
                    continue
                if type(ident) is int:
                    seen[ident] = row
                unique.append(row)
            raw["shots"] = unique
    notes = raw.get("continuity_issues")
    if isinstance(notes, str) and notes.strip():
        raw["continuity_issues"] = [notes]
        audit["conversions"].append("advisory_text_list")
    elif isinstance(notes, list):
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                continue
            message = note.get("message", note.get("description"))
            if (not isinstance(message, str) or not message.strip()
                    or ("description" in note and "message" in note and note["description"] != message)):
                raise ValidationError("连续性提醒必须是明确文本，不能丢弃冲突或缺失说明")
            notes[index] = message
            audit["conversions"].append("advisory_object_text")
    try:
        wire = SegmentWireOutput.model_validate(raw, strict=True)
    except SchemaError as exc:
        raise ValidationError("片段创作响应结构不完整",
                              details={"errors": exc.errors(include_input=False, include_url=False)[:10]}) from exc
    boundary = payload["director_pipeline"]["segment_outline"]
    if wire.segment_key != boundary["key"]:
        raise ValidationError("片段稳定标识与冻结边界不一致")
    expected = [row["shot_id"] for row in source if not row.get("is_locked")]
    if [row.shot_id for row in wire.shots] != expected:
        raise ValidationError("创作响应必须按顺序各一次覆盖未锁定镜头，不能遗漏、重复或改写锁定镜头",
                              details={"failure_kind": "shot_coverage", "expected": expected,
                                       "received": [row.shot_id for row in wire.shots]})
    if [row["shot_id"] for row in source] != boundary["shot_ids"]:
        raise ValidationError("冻结镜头与片段边界不一致")
    creative = {row.shot_id: row.model_dump() for row in wire.shots}
    shots = []
    fields = set(DirectorShotProposal.model_fields)
    for row in source:
        owned = {key: row[key] for key in fields if key in row}
        owned.update(creative.get(row["shot_id"], {}))
        owned.update(duration=row["duration"], dialogue=row.get("dialogue") or "", audio_note=row.get("audio_note") or "")
        shots.append(owned)
    scenes = {row["scene_id"]: {"scene_id": row["scene_id"], "name": row.get("scene_name"),
                              "location": row.get("scene_location"), "time_of_day": row.get("scene_time_of_day"),
                              "description": row.get("scene_description")} for row in source}
    semantic = {row["shot_id"]: {**source_by_id[row["shot_id"]], **row} for row in shots}
    script = build_structured_script(boundary["shot_ids"], semantic, scenes,
                                     entry_state=wire.entry_state, exit_state=wire.exit_state)
    assembled = DirectorModelOutput.model_validate({
        "shots": shots,
        "segments": [{"title": boundary["title"], "shot_ids": boundary["shot_ids"],
                      "generation_duration": boundary["generation_duration"], "prompt": compile_prompt(script),
                      "entry_state": wire.entry_state, "exit_state": wire.exit_state,
                      "negative_prompt": wire.negative_prompt, "parameters": {}}],
        "continuity_issues": wire.continuity_issues,
    }, strict=True)
    return assembled, audit
