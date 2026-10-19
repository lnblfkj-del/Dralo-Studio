"""Deterministic voice semantics, independent of human appearance and asset existence."""

import hashlib
import re

DEVICE = re.compile(
    r"(?:设备语音|电子播报|设备播报|广播语音|广播|广播声|系统提示音|"
    r".*(?:耳机|广播|收音机|电视|电话|手机|扬声器|音箱|播放器|导航|系统)"
    r"(?:声音|语音|播报|导航)?|收款播报)$"
)
SOUND = {"门铃", "警报声", "狗叫声", "脚步声", "雨声", "手机铃声", "关门声", "提示音"}
AMBIENCE = {"雨声", "风声", "海浪声"}
NOISE = re.compile(r"^[\s\W]*(?:滋|滴|嘀|叮|咚|嗡|呜|哔|吱|嘎|啪|砰|轰|沙|汪|喵)+[\s\W]*$")
LOCATIONS = {"on_screen", "off_screen", "subjective", "unspecified"}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def physical_lines(script):
    """Offsets use browser UTF-16 units, not Python code-point indices."""
    offset, rows = 0, []
    for number, raw in enumerate(script.splitlines(keepends=True), 1):
        text = raw.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        size = len(text.encode("utf-16-le")) // 2
        rows.append({"line": number, "text": text, "start": offset, "end": offset + size,
                     "line_fingerprint": digest(text)})
        offset += len(raw.encode("utf-16-le")) // 2
    return rows


def location(directions):
    words = " ".join(directions).casefold()
    if any(word in words for word in ("内心", "心声")) or re.search(r"(?<![a-z])o\.?s\.?(?![a-z])", words):
        return "subjective"
    if any(word in words for word in ("画外", "不入镜", "offscreen", "off-screen")) or re.search(r"(?<![a-z])v\.?o\.?(?![a-z])", words):
        return "off_screen"
    return "unspecified"


def guidance(voice):
    kind, place = voice.get("speaker_kind"), voice.get("location", "unspecified")
    parts = []
    if kind == "device":
        parts.append("由标注设备发声，不转成画面人物对白，不要求人物口型")
    elif kind == "anonymous":
        parts.append("独立匿名声音，不绑定主角或虚构角色资产")
    elif kind == "narration":
        parts.append("旁白，不作为画面人物台词")
    if place == "off_screen":
        parts.append("声音来自画外，不为此添加入镜人物或口型")
    elif place == "subjective":
        parts.append("内心或主观声音，不表现为人物张嘴说话")
    return "；".join(parts)
