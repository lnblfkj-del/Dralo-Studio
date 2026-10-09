"""Canonical structured segment scripts and deterministic prompt compilation."""

from __future__ import annotations

import re
from copy import deepcopy
from itertools import pairwise
from typing import Any

from app.core.errors import ValidationError

STRUCTURED_SCRIPT_KEY = "structured_script"
STRUCTURED_SCRIPT_VERSION = 1
DIALOGUE_LAYOUT = "source_lines.v1"
AI_SOURCE_TYPES = {
    "ai", "replan", "optimize_segment", "fill_empty", "optimize_selected", "split", "merge", "add",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError(f"{label}必须是对象数组")
    return [deepcopy(item) for item in value]


def _covered(records: list[dict[str, Any]], shot_ids: list[int], label: str) -> None:
    try:
        submitted = [int(item["shot_id"]) for item in records]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"{label}缺少有效分镜编号") from exc
    if submitted != shot_ids:
        raise ValidationError(f"{label}必须按顺序且各一次覆盖片段全部分镜")


def parse_dialogue(value: Any) -> tuple[str, str, bool]:
    text = _text(value)
    if not text:
        return "", "", False
    match = re.match(r"^\s*([^：:\n]{1,80})\s*[：:]\s*(.+?)\s*$", text, re.S)
    if match:
        return match.group(1).strip(), match.group(2).strip(), True
    return "", text, False


def quoted_dialogue_closed(utterance: str) -> bool:
    if not utterance.startswith(('"', '“')):
        return True
    opening = utterance[0]
    depth = 1
    escaped = False
    for character in utterance[1:]:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if opening == '“' and character == '“':
            depth += 1
        elif character == ('”' if opening == '“' else '"'):
            depth -= 1
            if depth == 0:
                return True
    return False


def _dialogue_sources(value: Any) -> list[tuple[str, str, str, bool]]:
    text = _text(value)
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed = []
    index = 0
    while index < len(lines):
        first = lines[index]
        speaker, utterance, owned = parse_dialogue(first)
        index += 1
        group = [first]
        if (not owned and re.fullmatch(r"[^：:\n]{1,30}[：:]\s*", first)
                and index < len(lines) and lines[index].startswith(('"', '“'))):
            group.append(lines[index])
            speaker, utterance, owned = parse_dialogue("\n".join(group))
            index += 1
        if not owned:
            return [(text, *parse_dialogue(text))]
        if utterance.startswith(('"', '“')):
            quoted = utterance
            while not quoted_dialogue_closed(quoted):
                if index >= len(lines) or re.match(r'^[^：:\n]{1,30}[：:]\s*(?:["“]|$)', lines[index]):
                    return [(text, *parse_dialogue(text))]
                group.append(lines[index])
                quoted += "\n" + lines[index]
                index += 1
        source = "\n".join(group)
        parsed.append((source, *parse_dialogue(source)))
    return parsed


def _audio_entries(shot_id: int, value: Any) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "music": [],
        "ambience": [],
        "sound_effects": [],
    }
    source = _text(value)
    if not source:
        return result
    parts = [item.strip() for item in re.split(r"[；;\n]+", source) if item.strip()]
    for part in parts:
        lowered = part.lower()
        if re.search(r"^(配乐|音乐|bgm)\s*[：:]?", part, re.I):
            kind = "music"
        elif re.search(r"^(音效|sfx)\s*[：:]?", part, re.I):
            kind = "sound_effects"
        elif re.search(r"^(环境声|氛围|ambience|ambient|room tone)\s*[：:]?", part, re.I):
            kind = "ambience"
        elif any(token in lowered for token in ("music", "bgm", "配乐", "音乐")):
            kind = "music"
        elif any(token in lowered for token in ("sfx", "音效", "声响")):
            kind = "sound_effects"
        else:
            kind = "ambience"
        result[kind].append({"shot_id": shot_id, "text": part, "source_note": source})
    return result


def build_structured_script(
    shot_ids: list[int],
    shots: dict[int, dict[str, Any]],
    scenes: dict[int, dict[str, Any]],
    *,
    entry_state: str = "",
    exit_state: str = "",
    model_proposed_states: bool = False,
) -> dict[str, Any]:
    ordered = [shots[shot_id] for shot_id in shot_ids]
    if not ordered:
        raise ValidationError("片段至少需要一个分镜")
    scene_ids = {int(item["scene_id"]) for item in ordered}
    if len(scene_ids) != 1:
        raise ValidationError("结构化片段脚本不能跨场景")
    scene = scenes.get(next(iter(scene_ids)))
    if scene is None:
        raise ValidationError("片段来源场景不存在")

    dialogue: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    audio = {"music": [], "ambience": [], "sound_effects": []}
    for item in ordered:
        shot_id = int(item["shot_id"])
        for source_text, speaker, line, owned in _dialogue_sources(item.get("dialogue")):
            proposed_speaker = _text(item.get("dialogue_speaker"))
            dialogue.append({
                "shot_id": shot_id,
                "speaker": speaker or proposed_speaker,
                "speaker_source": "source" if owned else "model_suggestion",
                "text": line,
                "tone": _text(item.get("dialogue_tone")) or "未指定",
                "source_text": source_text,
            })
            if not owned:
                issues.append({
                    "code": "dialogue_speaker_missing",
                    "severity": "blocking",
                    "shot_id": shot_id,
                    "message": (
                        f"这句台词的说话人建议为“{proposed_speaker}”，请人工确认后生成视频"
                        if proposed_speaker
                        else "来源台词没有明确说话人，必须人工确认后才能生成视频"
                    ),
                })
        classified = _audio_entries(shot_id, item.get("audio_note"))
        for key in audio:
            audio[key].extend(classified[key])

    first_action = _text(ordered[0].get("action"))
    last_action = _text(ordered[-1].get("action"))
    explicit_states = bool(_text(entry_state) and _text(exit_state))
    return {
        "schema_version": STRUCTURED_SCRIPT_VERSION,
        **({"dialogue_layout": DIALOGUE_LAYOUT}
           if any(len(_dialogue_sources(item.get("dialogue"))) > 1 for item in ordered) else {}),
        "source_shot_ids": shot_ids,
        "scene": {
            "scene_id": int(scene["scene_id"]),
            "name": _text(scene.get("name")),
            "location": _text(scene.get("location")),
            "time_of_day": _text(scene.get("time_of_day")),
            "description": _text(scene.get("description")),
        },
        "camera": [
            {
                "shot_id": int(item["shot_id"]),
                "shot_size": _text(item.get("shot_size")) or "未指定",
                "camera_angle": _text(item.get("camera_angle")) or "未指定",
                "camera_movement": _text(item.get("camera_movement")) or "未指定",
            }
            for item in ordered
        ],
        "performances": [
            {
                "shot_id": int(item["shot_id"]),
                "subject": _text(item.get("subject")) or "未指定主体",
                "expression": _text(item.get("expression")) or "未指定",
                "action": _text(item.get("action")) or "未指定动作",
            }
            for item in ordered
        ],
        "dialogue": dialogue,
        "audio": audio,
        "entry_state": _text(entry_state) or (f"开始：{first_action}" if first_action else "片段开始"),
        "exit_state": _text(exit_state) or (f"结束：{last_action}" if last_action else "片段结束"),
        "state_source": ("model_proposal" if model_proposed_states else "explicit") if explicit_states else "derived",
        "coverage": {
            "shot_count": len(shot_ids),
            "camera_count": len(shot_ids),
            "performance_count": len(shot_ids),
            "dialogue_count": len(dialogue),
        },
        "validation_issues": issues,
    }


def validate_structured_script(
    raw: dict[str, Any],
    shot_ids: list[int],
    shots: dict[int, dict[str, Any]],
    scenes: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != STRUCTURED_SCRIPT_VERSION:
        raise ValidationError("结构化片段脚本版本无效")
    try:
        source_ids = [int(value) for value in raw.get("source_shot_ids", [])]
    except (TypeError, ValueError) as exc:
        raise ValidationError("结构化片段脚本的来源分镜无效") from exc
    if source_ids != shot_ids:
        raise ValidationError("结构化片段脚本必须与片段分镜范围和顺序一致")

    scene_ids = {int(shots[shot_id]["scene_id"]) for shot_id in shot_ids}
    scene = raw.get("scene")
    if len(scene_ids) != 1 or not isinstance(scene, dict):
        raise ValidationError("结构化片段脚本场景无效")
    source_scene = scenes.get(next(iter(scene_ids)))
    if source_scene is None or int(scene.get("scene_id") or 0) != int(source_scene["scene_id"]):
        raise ValidationError("结构化片段脚本场景与来源分镜不一致")

    camera = _records(raw.get("camera"), "镜头语义")
    performances = _records(raw.get("performances"), "表演语义")
    dialogue = _records(raw.get("dialogue", []), "台词语义")
    _covered(camera, shot_ids, "镜头语义")
    _covered(performances, shot_ids, "表演语义")
    for record in camera:
        if any(
            not _text(record.get(field))
            for field in ("shot_size", "camera_angle", "camera_movement")
        ):
            raise ValidationError("每个分镜都必须包含景别、机位和运镜")
    for record in performances:
        if any(
            not _text(record.get(field))
            for field in ("subject", "expression", "action")
        ):
            raise ValidationError("每个分镜都必须包含主体、表情和动作")

    issues: list[dict[str, Any]] = []
    dialogue_layout = raw.get("dialogue_layout")
    if dialogue_layout not in (None, DIALOGUE_LAYOUT):
        raise ValidationError("结构化台词布局版本无效")
    by_dialogue: dict[int, list[dict[str, Any]]] = {}
    for record in dialogue:
        try:
            shot_id = int(record["shot_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("台词语义缺少有效分镜编号") from exc
        if shot_id not in shot_ids:
            raise ValidationError("台词语义引用了片段外分镜")
        by_dialogue.setdefault(shot_id, []).append(record)
    for shot_id in shot_ids:
        source = shots[shot_id].get("dialogue")
        sources = (_dialogue_sources(source) if dialogue_layout == DIALOGUE_LAYOUT
                   else [(_text(source), *parse_dialogue(source))] if _text(source) else [])
        records = by_dialogue.get(shot_id, [])
        if not sources:
            if records:
                raise ValidationError("结构化片段脚本不能编造来源中不存在的台词")
            continue
        if len(records) != len(sources):
            raise ValidationError("结构化片段脚本必须完整且原样覆盖来源台词")
        for record, (source_text, source_speaker, source_line, source_owned) in zip(records, sources):
            if record.get("text_source") != "manual" and _text(record.get("text")) != source_line:
                raise ValidationError("结构化片段脚本必须完整且原样覆盖来源台词")
            if not _text(record.get("text")):
                raise ValidationError("人工改编台词不能为空")
            record["source_text"] = source_text if dialogue_layout == DIALOGUE_LAYOUT else source_line
            speaker = _text(record.get("speaker"))
            if source_owned and speaker != source_speaker and record.get("speaker_source") != "manual":
                raise ValidationError("结构化片段脚本的说话人与来源台词不一致")
            if not speaker or (not source_owned and record.get("speaker_source") != "manual"):
                issues.append({
                    "code": "dialogue_speaker_missing",
                    "severity": "blocking",
                    "shot_id": shot_id,
                    "message": (
                        f"这句台词的说话人建议为“{speaker}”，请人工确认后生成视频"
                        if speaker
                        else "来源台词没有明确说话人，必须人工确认后才能生成视频"
                    ),
                })
            if not _text(record.get("tone")):
                raise ValidationError("每条结构化台词都必须包含语气")

    audio = raw.get("audio")
    if not isinstance(audio, dict):
        raise ValidationError("结构化片段脚本缺少声音语义")
    audio_records: list[dict[str, Any]] = []
    for key in ("music", "ambience", "sound_effects"):
        audio_records.extend(_records(audio.get(key, []), f"声音语义.{key}"))
    for record in audio_records:
        try:
            audio_shot_id = int(record["shot_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("声音语义缺少有效分镜编号") from exc
        if audio_shot_id not in shot_ids or not _text(record.get("text")):
            raise ValidationError("声音语义引用片段外分镜或内容为空")
        record["shot_id"] = audio_shot_id
    for shot_id in shot_ids:
        source_note = _text(shots[shot_id].get("audio_note"))
        if source_note and not any(
            int(item.get("shot_id") or 0) == shot_id
            and (_text(item.get("source_note")) == source_note or _text(item.get("text")) == source_note)
            for item in audio_records
        ):
            raise ValidationError("结构化片段脚本遗漏了来源配乐、环境声或音效")

    normalized = deepcopy(raw)
    normalized["scene"] = {
        "scene_id": int(source_scene["scene_id"]),
        "name": _text(source_scene.get("name")),
        "location": _text(source_scene.get("location")),
        "time_of_day": _text(source_scene.get("time_of_day")),
        "description": _text(source_scene.get("description")),
    }
    if raw.get("scene_source") == "manual" and isinstance(raw.get("scene"), dict):
        normalized["scene"].update({
            key: _text(raw["scene"].get(key))
            for key in ("name", "location", "time_of_day", "description")
        })
    normalized["camera"] = camera
    normalized["performances"] = performances
    normalized["dialogue"] = dialogue
    normalized["audio"] = {key: _records(audio.get(key, []), f"声音语义.{key}") for key in ("music", "ambience", "sound_effects")}
    normalized["entry_state"] = _text(raw.get("entry_state"))
    normalized["exit_state"] = _text(raw.get("exit_state"))
    if not normalized["entry_state"] or not normalized["exit_state"]:
        raise ValidationError("结构化片段脚本必须包含进入和结束状态")
    normalized["state_source"] = raw.get("state_source") if raw.get("state_source") in {"explicit", "model_proposal"} else "derived"
    normalized["coverage"] = {
        "shot_count": len(shot_ids),
        "camera_count": len(camera),
        "performance_count": len(performances),
        "dialogue_count": len(dialogue),
    }
    normalized["validation_issues"] = issues
    return normalized


def compile_prompt(script: dict[str, Any]) -> str:
    scene = script["scene"]
    scene_parts = [scene.get("name"), scene.get("location"), scene.get("time_of_day")]
    lines = ["场景：" + " / ".join(_text(item) for item in scene_parts if _text(item))]
    if _text(scene.get("description")):
        lines.append("场景说明：" + _text(scene.get("description")))
    lines.append("镜头：")
    for item in script["camera"]:
        duration = f"，时长{item['duration']}秒" if item.get("duration") is not None else ""
        lines.append(
            f"- 分镜{item['shot_id']}：{_text(item.get('shot_size'))}，"
            f"{_text(item.get('camera_angle'))}，{_text(item.get('camera_movement'))}{duration}"
        )
    lines.append("表演：")
    for item in script["performances"]:
        lines.append(
            f"- 分镜{item['shot_id']}：{_text(item.get('subject'))}，"
            f"表情{_text(item.get('expression'))}，动作{_text(item.get('action'))}"
        )
    if script["dialogue"]:
        lines.append("对白：")
        for item in script["dialogue"]:
            speaker = _text(item.get("speaker")) or "待确认说话人"
            lines.append(f"- 分镜{item['shot_id']}：{speaker}（{_text(item.get('tone'))}）：{_text(item.get('text'))}")
    audio_lines = []
    labels = {"music": "配乐", "ambience": "环境声", "sound_effects": "音效"}
    for key, label in labels.items():
        values = script["audio"].get(key, [])
        if values:
            audio_lines.append(f"- {label}：" + "；".join(_text(item.get("text")) for item in values))
    if audio_lines:
        lines.append("声音：")
        lines.extend(audio_lines)
    lines.append(f"状态：进入[{script['entry_state']}]；结束[{script['exit_state']}]")
    return "\n".join(lines)


def normalize_segment(
    item: dict[str, Any],
    shot_ids: list[int],
    shots: dict[int, dict[str, Any]],
    scenes: dict[int, dict[str, Any]],
    *,
    force_structure: bool,
) -> dict[str, Any]:
    parameters = deepcopy(item.get("parameters") or {})
    raw = parameters.get(STRUCTURED_SCRIPT_KEY)
    if isinstance(raw, dict) and raw.get("editor_document") is not None:
        from app.services.segment_document_service import compile_document
        script, prompt, duration = compile_document(raw, shot_ids, (item.get("refs") or {}).get("asset_bindings") or [])
        if abs(duration - float(item.get("timeline_duration") or item["generation_duration"])) > 0.05:
            raise ValidationError("正文镜头时长合计与片段时长不一致")
        parameters[STRUCTURED_SCRIPT_KEY] = script
        return {**item, "parameters": parameters, "prompt": prompt}
    if raw is None and not force_structure:
        return {**item, "parameters": parameters}
    script = (
        validate_structured_script(raw, shot_ids, shots, scenes)
        if raw is not None
        else build_structured_script(shot_ids, shots, scenes)
    )
    bindings = (item.get("refs") or {}).get("asset_bindings") or []
    documents = script.get("document_fields") or {}
    if not isinstance(documents, dict):
        raise ValidationError("正文编辑数据无效")
    pending = list(documents.values())
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            raise ValidationError("正文编辑节点无效")
        if node.get("type") == "text" and "@" in str(node.get("text") or ""):
            raise ValidationError("正文中有未确认的素材引用，请选择素材或移除 @")
        if node.get("type") == "assetMention":
            attrs = node.get("attrs") or {}
            if not any(
                binding.get("asset_id") == attrs.get("asset_id")
                and binding.get("asset_version_id") == attrs.get("asset_version_id")
                and binding.get("resolved") is not False
                for binding in bindings
            ):
                raise ValidationError("正文引用的素材版本尚未绑定，请重新选择素材")
        pending.extend(node.get("content") or [])
    parameters[STRUCTURED_SCRIPT_KEY] = script
    return {**item, "parameters": parameters, "prompt": compile_prompt(script)}


def continuity_issues(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    structured: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for segment in segments:
        script = (segment.get("parameters") or {}).get(STRUCTURED_SCRIPT_KEY)
        structured.append((segment, script if isinstance(script, dict) else None))
        if not isinstance(script, dict):
            issues.append({
                "code": "legacy_text_script",
                "severity": "warning",
                "segment_id": segment.get("id"),
                "message": "片段仅有旧版文本提示词，尚未建立结构化脚本语义",
            })
            continue
        for issue in script.get("validation_issues") or []:
            issues.append({**issue, "segment_id": segment.get("id")})
    for (previous, left), (current, right) in pairwise(structured):
        if not left or not right:
            continue
        previous_scene = left.get("scene") if isinstance(left.get("scene"), dict) else {}
        current_scene = right.get("scene") if isinstance(right.get("scene"), dict) else {}
        scene_change = (previous_scene.get("scene_id") is not None and current_scene.get("scene_id") is not None
                        and previous_scene["scene_id"] != current_scene["scene_id"])
        if (left.get("state_source") == "explicit" and right.get("state_source") == "explicit"
                and _text(left.get("exit_state")) != _text(right.get("entry_state"))):
            issues.append({
                "code": "state_discontinuity",
                "severity": "blocking",
                "segment_id": current.get("id"),
                "previous_segment_id": previous.get("id"),
                "message": "前一片段结束状态与当前片段进入状态不一致",
            })
        elif (not scene_change and "model_proposal" in {left.get("state_source"), right.get("state_source")}
              and _text(left.get("exit_state")) and _text(right.get("entry_state"))
              and _text(left["exit_state"]) != _text(right["entry_state"])):
            # Different generated descriptions are not proof of a physical conflict.
            issues.append({"code": "state_handoff_review", "severity": "warning",
                           "segment_id": current.get("id"), "previous_segment_id": previous.get("id"),
                           "message": "AI建议的前后状态描述不同，请核对人物位置、道具和动作交接；尚未验证语义一致"})
        if previous_scene.get("scene_id") is not None and previous_scene.get("scene_id") == current_scene.get("scene_id"):
            for field, label in (("location", "地点"), ("time_of_day", "时段")):
                before, after = _text(previous_scene.get(field)), _text(current_scene.get(field))
                if before and after and before != after:
                    issues.append({
                        "code": "scene_context_change",
                        "severity": "warning",
                        "segment_id": current.get("id"),
                        "previous_segment_id": previous.get("id"),
                        "message": f"同一场景相邻片段的{label}不同，请核对是否有明确转换",
                    })
        previous_ratio = _text((previous.get("parameters") or {}).get("aspect_ratio"))
        current_ratio = _text((current.get("parameters") or {}).get("aspect_ratio"))
        if previous_ratio not in {"", "default", "project"} and current_ratio not in {"", "default", "project"} and previous_ratio != current_ratio:
            issues.append({
                "code": "aspect_ratio_change",
                "severity": "warning",
                "segment_id": current.get("id"),
                "previous_segment_id": previous.get("id"),
                "message": "相邻片段的显式画幅比例不同，请核对项目画幅",
            })
        for asset_id in _changed_asset_versions(previous, current):
            issues.append({
                "code": "asset_version_change",
                "severity": "warning",
                "segment_id": current.get("id"),
                "previous_segment_id": previous.get("id"),
                "asset_id": asset_id,
                "message": "同一资产在相邻片段采用了不同版本，请核对造型或道具转换",
            })
        for kind, label in (("music", "配乐"), ("ambience", "环境声")):
            before = _audio_texts(left, kind)
            after = _audio_texts(right, kind)
            if before and after and before != after:
                issues.append({
                    "code": "audio_transition",
                    "severity": "warning",
                    "segment_id": current.get("id"),
                    "previous_segment_id": previous.get("id"),
                    "audio_kind": kind,
                    "message": f"相邻片段的{label}描述不同，请核对声音衔接",
                })
    return issues


def _changed_asset_versions(previous: dict[str, Any], current: dict[str, Any]) -> list[int]:
    def versions(segment: dict[str, Any]) -> dict[tuple[int, str], int]:
        result = {}
        for item in (segment.get("refs") or {}).get("asset_bindings") or []:
            if not isinstance(item, dict):
                continue
            asset_id, version_id = item.get("asset_id"), item.get("asset_version_id")
            if type(asset_id) is int and type(version_id) is int:
                result[(asset_id, _text(item.get("role")))] = version_id
        return result

    before, after = versions(previous), versions(current)
    return sorted({key[0] for key in before.keys() & after.keys() if before[key] != after[key]})


def _audio_texts(script: dict[str, Any], kind: str) -> set[str]:
    audio = script.get("audio") if isinstance(script.get("audio"), dict) else {}
    return {
        text for item in audio.get(kind) or []
        if isinstance(item, dict) and (text := _text(item.get("text")))
    }
