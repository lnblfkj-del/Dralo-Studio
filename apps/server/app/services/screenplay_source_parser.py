"""Lossless screenplay classification. Identity ambiguity is never an action fallback."""

import re

from app.core.errors import ValidationError

VERSION = "screenplay.sources.v1"
PAIRS = {"（": "）", "(": ")", "【": "】", "[": "]"}
SCENE = re.compile(r"(?:场景|场次)[\s\d一二三四五六七八九十百零_-]*$")
SCENE_SLUG = re.compile(
    r"^\d{1,4}\s*[-－—]\s*\d{1,4}\s+.+?\s+(?:"
    r"(?:内|外|内景|外景)\s+(?:日|夜|昼|晨|白天|晚上|清晨|黄昏|傍晚)|"
    r"(?:日|夜|昼|晨|白天|晚上|清晨|黄昏|傍晚)\s+(?:内|外|内景|外景))$"
)
META = re.compile(r"(?:第[\d一二三四五六七八九十百]+集|地点|时间|人物|出场人物|角色|标题|集数|时长|集尾钩子|备注|说明|注)$")
ACTION = {"动作", "画面", "画面定格", "定格", "剧情", "分镜", "镜头"}
AUDIO = {"bgm": "music", "配乐": "music", "配乐bgm": "music", "音乐": "music",
         "背景音乐": "music", "音效": "sound_effects", "sfx": "sound_effects",
         "环境声": "ambience", "环境音": "ambience", "氛围": "ambience", "剧情内音乐": "diegetic_music"}
NARRATORS = {"旁白", "画外音", "解说", "独白", "vo", "v.o."}
DEVICE = re.compile(r"(?:.*(?:耳机|广播|收音机|电视|电话|扬声器|系统)(?:声音|语音|播报)|广播|广播声|系统提示音)$")


def bracket_end(text):
    if not text or text[0] not in PAIRS:
        return None
    stack = []
    for index, char in enumerate(text):
        if char in PAIRS:
            stack.append(PAIRS[char])
        elif char in PAIRS.values():
            if not stack or stack.pop() != char:
                return None
            if not stack:
                return index
    return None


def unwrap(text):
    text = text.strip()
    while text and bracket_end(text) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def split_cue(text):
    """Find the delimiter outside nested performance directions, with no cue truncation."""
    stack = []
    for index, char in enumerate(text):
        if char in PAIRS:
            stack.append(PAIRS[char])
        elif char in PAIRS.values():
            if not stack or stack.pop() != char:
                return None
        elif char in ":：" and not stack:
            return text[:index].strip(), text[index + 1:].strip()
    return None


def name_and_directions(cue):
    start = next((i for i, char in enumerate(cue) if char in PAIRS), len(cue))
    name, rest, directions = cue[:start].strip(), cue[start:].strip(), []
    while rest:
        end = bracket_end(rest)
        if end is None:
            return cue, []
        directions.append(rest[1:end])
        rest = rest[end + 1:].strip()
    return name, directions


def spoken_parts(cue, body):
    name, directions = name_and_directions(cue)
    while body and body[0] in "（(":
        end = bracket_end(body)
        if end is None:
            break
        directions.append(body[1:end])
        body = body[end + 1:].strip()
    return name, directions, body


def identity_index(catalog):
    index, canonical = {}, {}
    for position, item in enumerate(catalog):
        if item.get("asset_type") != "character":
            continue
        identity = item.get("asset_id", f"catalog:{position}")
        canonical[identity] = item.get("asset_name") or ""
        for name in [item.get("asset_name"), *(item.get("aliases") or [])]:
            if not isinstance(name, str) or not name.strip():
                continue
            for key in (name.strip(), name_and_directions(name.strip())[0]):
                index.setdefault(key.casefold(), set()).add(identity)
    return index, canonical


def fail(line, reason):
    raise ValidationError(f"正文第 {line} 行{reason}，请核对原文，不会省略该行",
                          details={"failure_kind": "screenplay_source_ambiguous", "source_line": line})


def classify(text, identities, scene_people, line):
    value = unwrap(text)
    if SCENE_SLUG.fullmatch(value):
        return {"kind": "scene"}
    cue_body = split_cue(re.sub(r"^#+\s*", "", value))
    # A labelled visual paragraph can itself include colon punctuation.
    if not cue_body:
        if re.match(r"^(?:场景|场次)[\s\d一二三四五六七八九十百零_-]+", value):
            return {"kind": "scene"}
        if any(char in value for char in ":：") and not value.startswith(tuple(f"（{v}）" for v in ACTION)):
            fail(line, "的括号或说话人说明不明确")
        return {"kind": "action"}
    cue, body = cue_body
    if SCENE.fullmatch(cue):
        return {"kind": "scene"}
    if META.fullmatch(cue):
        return {"kind": "metadata", "label": cue, "value": body}
    if cue in ACTION:
        return {"kind": "action"}
    if cue.casefold() in AUDIO:
        return {"kind": AUDIO[cue.casefold()], "field": "audio_note"}
    # Prefix markers such as (动作) belong to the whole visual paragraph.
    if any(value.startswith(f"{opening}{word}{closing}")
           for opening, closing in (("（", "）"), ("(", ")")) for word in ACTION):
        return {"kind": "action"}
    name, directions, spoken = spoken_parts(cue, body)
    references = []
    if name.casefold() in NARRATORS:
        speaker_kind = "narration"
    elif DEVICE.fullmatch(name):
        speaker_kind = "device"
    else:
        labels = re.split(r"[/／、与和]", name)
        # A literal canonical name wins over interpreting a separator within it.
        if name.casefold() in identities:
            labels = [name]
        if name in {"两人", "二人", "两位"}:
            if len(scene_people) != 2 or None in scene_people:
                fail(line, "的合说主体不明确")
            references = sorted(scene_people, key=str)
            speaker_kind = "group"
        else:
            for label in labels:
                candidates = identities.get(label.strip().casefold(), set())
                if len(candidates) > 1:
                    fail(line, "的说话人对应多个角色")
                if not candidates:
                    # Preserve explicitly quoted speech without inventing an asset.
                    if len(labels) == 1 and spoken.startswith(('“', '"')):
                        speaker_kind = "unbound"
                        break
                    fail(line, "的说话人或说明不明确")
                references.extend(candidates)
            else:
                speaker_kind = "group" if len(labels) > 1 else "character"
            if len(references) != len(set(references)):
                fail(line, "的合说主体重复")
    if directions and not spoken:
        return {"kind": "action", "stage_directions": directions}
    return {"kind": "dialogue", "field": "dialogue", "speaker": name,
            "speaker_kind": speaker_kind, "speaker_refs": references,
            "stage_directions": directions, "spoken_text": spoken}


def parse_sources(rows, catalog):
    from app.services.segment_script_semantics import quoted_dialogue_closed

    identities, canonical = identity_index(catalog)
    scene_people, records = set(), []
    index = 0
    while index < len(rows):
        row = rows[index]
        info = classify(row["text"], identities, scene_people, row["line"])
        group = [row]
        index += 1
        if info["kind"] == "scene":
            scene_people.clear()
        if info.get("label") in {"人物", "出场人物", "角色"}:
            for label in re.split(r"[、,，/／]", info["value"]):
                key = name_and_directions(label.strip())[0].casefold()
                if len(identities.get(key, set())) != 1:
                    scene_people.add(None)
        if info["kind"] == "action" or info.get("label") in {"人物", "出场人物", "角色"}:
            # Only explicit visual/metadata mentions establish generic group context.
            for identity, name in canonical.items():
                key = name_and_directions(name)[0]
                if key and key in row["text"]:
                    scene_people.add(identity)
        if info["kind"] == "dialogue":
            scene_people.update(info["speaker_refs"])
            spoken = info["spoken_text"]
            if not spoken:
                if index >= len(rows) or not rows[index]["text"].lstrip().startswith(('"', '“')):
                    fail(row["line"], "的角色提示后缺少明确的带引号台词")
                spoken = rows[index]["text"].lstrip()
                group.append(rows[index])
                index += 1
            while not quoted_dialogue_closed(spoken):
                if index >= len(rows):
                    fail(row["line"], "的带引号台词未闭合")
                following = rows[index]
                # A new labelled source must never be swallowed as quote continuation.
                if split_cue(unwrap(following["text"])) and not quoted_dialogue_closed(spoken + "\n" + following["text"]):
                    fail(row["line"], "的带引号台词未闭合")
                next_cue = split_cue(unwrap(following["text"]))
                if next_cue:
                    name = name_and_directions(next_cue[0])[0]
                    if (name.casefold() in identities or name.casefold() in AUDIO
                            or name.casefold() in NARRATORS or DEVICE.fullmatch(name)
                            or not META.fullmatch(name) or next_cue[1].startswith(('“', '"'))):
                        fail(row["line"], "的带引号台词未闭合")
                group.append(following)
                spoken += "\n" + following["text"]
                index += 1
            info["spoken_text"] = spoken
        records.append({**info, "line": row["line"], "source_lines": [r["line"] for r in group],
                        "text": "\n".join(r["text"] for r in group)})
    return records
