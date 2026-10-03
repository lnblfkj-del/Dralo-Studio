"""Read and structurally analyse bounded script references without executing them."""

import re
from io import BytesIO
from pathlib import PurePosixPath
from statistics import median
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zlib import error as zlib_error

from app.core.errors import ValidationError
from app.core.creation_limits import (
    MAX_EPISODES, MAX_REFERENCE_BYTES, MAX_SOURCE_CHARACTERS,
    MAX_DOCX_XML_BYTES, MAX_DOCX_STYLES_BYTES, MAX_DOCX_UNCOMPRESSED_BYTES,
)

MAX_FILE_BYTES = MAX_REFERENCE_BYTES
MAX_XML_BYTES = MAX_DOCX_XML_BYTES
MAX_CHARACTERS = MAX_SOURCE_CHARACTERS
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_EPISODE_HEADING = re.compile(
    r"^(?:第\s*(?P<cn>[0-9零〇两一二三四五六七八九十百千]+)\s*集|(?:EPISODE|EP)\s*0*(?P<ep>[1-9][0-9]*))\s*[:：.、]?\s*(?P<title>[^\n]{0,180})$",
    re.IGNORECASE,
)
_EPISODE_RANGE = re.compile(
    r"(?i)(?:EPISODE|EP)\s*0*[1-9][0-9]*\s*[-–—~至到]\s*(?:(?:EPISODE|EP)\s*)?0*[1-9][0-9]*"
)


def _chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = dict(zip("零一二三四五六七八九", range(10))) | {"〇": 0, "两": 2}
    if all(char in digits for char in value):
        return int("".join(str(digits[char]) for char in value))
    total, digit, last_unit = 0, 0, 10000
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            if unit >= last_unit:
                return None
            total += (digit or 1) * unit
            digit, last_unit = 0, unit
        else:
            return None
    return total + digit


def _heading(line: str) -> tuple[int, str] | None:
    value = line.strip()
    if not value or len(value) > 200 or _EPISODE_RANGE.search(value):
        return None
    matched = _EPISODE_HEADING.fullmatch(value)
    if not matched:
        return None
    number = int(matched.group("ep")) if matched.group("ep") else _chinese_number(matched.group("cn"))
    if number is None:
        return None
    if not 1 <= number <= MAX_EPISODES:
        raise ValidationError(f"发现第 {number} 集，分集编号必须在 1 到 {MAX_EPISODES} 之间")
    title = matched.group("title").strip(" \t:：.、-—《》")
    return number, title or f"第 {number} 集"


def _line_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        parsed = _heading(line.rstrip("\r\n"))
        if parsed:
            candidates.append({"number": parsed[0], "title": parsed[1], "start": offset})
        offset += len(line)
    if text and not text.endswith(("\n", "\r")):
        offset = len(text)
    for index, item in enumerate(candidates):
        item["end"] = candidates[index + 1]["start"] if index + 1 < len(candidates) else len(text)
        item["char_count"] = len(text[item["start"]:item["end"]].strip())
    return candidates


def _sequence_groups(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in candidates:
        if not current or item["number"] == current[-1]["number"] + 1:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)
    return groups


def analyze_script_structure(
    text: str, preferred_starts: list[int] | None = None
) -> dict[str, Any]:
    """Return one validated episode sequence; never invent equal-sized episodes."""
    candidates = _line_candidates(text)
    if preferred_starts:
        starts = set(preferred_starts)
        preferred = [item for item in candidates if item["start"] in starts]
        groups = _sequence_groups(preferred)
        mode = "docx_headings"
    else:
        groups = _sequence_groups(candidates)
        mode = "strict_headings"
    valid = [group for group in groups if group and group[0]["number"] == 1]
    if valid:
        chosen = max(
            valid,
            key=lambda group: (
                len(group),
                median(item["char_count"] for item in group),
                sum(min(item["char_count"], 12000) for item in group),
            ),
        )
        ordered = []
        for item in chosen:
            following = item["end"]
            ordered.append({**item, "end": following, "char_count": len(text[item["start"]:following].strip())})
        confidence = "high" if mode == "docx_headings" or median(item["char_count"] for item in ordered) >= 120 else "medium"
        return {
            "mode": mode,
            "confidence": confidence,
            "detected_episode_count": len(ordered),
            "episodes": ordered,
            "warnings": [] if confidence == "high" else ["分集标题已识别，但部分集正文较短，请创建后复核。"],
        }
    return {
        "mode": "single_source",
        "confidence": "needs_ai",
        "detected_episode_count": 1,
        "episodes": [{"number": 1, "title": "待 AI 拆分", "start": 0, "end": len(text), "char_count": len(text.strip())}],
        "warnings": ["未发现可信的连续分集标题，系统将保留为一份原稿，不再按段落强行拆分。"],
    }


def _docx_text(archive: ZipFile, xml: bytes) -> tuple[str, list[int]]:
    document = ElementTree.fromstring(xml)
    heading_style_ids = {"Heading1"}
    try:
        info = archive.getinfo("word/styles.xml")
        if info.file_size > MAX_DOCX_STYLES_BYTES or info.flag_bits & 1:
            raise ValidationError("DOCX 样式文件过大或已加密，请改用纯文本")
        with archive.open(info) as source:
            style_xml = source.read(MAX_DOCX_STYLES_BYTES + 1)
        if len(style_xml) > MAX_DOCX_STYLES_BYTES or b"\x00" in style_xml or b"<!DOCTYPE" in style_xml.upper() or b"<!ENTITY" in style_xml.upper():
            raise ValidationError("DOCX 样式包含不支持的 XML 内容")
        styles = ElementTree.fromstring(style_xml)
        for style in styles.iter(_WORD_NS + "style"):
            style_id = style.get(_WORD_NS + "styleId", "")
            name = style.find(_WORD_NS + "name")
            outline = style.find(".//" + _WORD_NS + "outlineLvl")
            if (name is not None and name.get(_WORD_NS + "val", "").lower() == "heading 1") or (
                outline is not None and outline.get(_WORD_NS + "val") == "0"
            ):
                heading_style_ids.add(style_id)
    except (KeyError, ElementTree.ParseError):
        pass
    paragraphs: list[str] = []
    preferred_raw: list[int] = []
    raw_offset = 0
    for paragraph in document.iter(_WORD_NS + "p"):
        value = "".join(
            node.text or "" if node.tag == _WORD_NS + "t" else "\t" if node.tag == _WORD_NS + "tab" else "\n" if node.tag in {_WORD_NS + "br", _WORD_NS + "cr"} else ""
            for node in paragraph.iter()
        )
        style = paragraph.find("./" + _WORD_NS + "pPr/" + _WORD_NS + "pStyle")
        if style is not None and style.get(_WORD_NS + "val") in heading_style_ids and _heading(value):
            preferred_raw.append(raw_offset)
        paragraphs.append(value)
        raw_offset += len(value) + 1
    raw = "\n".join(paragraphs)
    return raw, preferred_raw


def read_reference(name: str, data: bytes) -> dict[str, Any]:
    name = PurePosixPath(name.replace("\\", "/")).name[:255]
    suffix = PurePosixPath(name).suffix.lower()
    preferred_starts: list[int] | None = None
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError("参考文件不能超过 20 MiB（20 × 1024 × 1024 字节）")
    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("请将文本文件转换为 UTF-8 编码") from exc
    elif suffix == ".docx":
        try:
            with ZipFile(BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > 10000 or sum(member.file_size for member in members) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise ValidationError("DOCX 解压内容超过安全容量，请移除内嵌媒体或改用纯文本")
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_XML_BYTES or info.flag_bits & 1:
                    raise ValidationError("DOCX 正文过大或已加密，请改用纯文本")
                with archive.open(info) as source:
                    xml = source.read(MAX_XML_BYTES + 1)
                if len(xml) > MAX_XML_BYTES or b"\x00" in xml or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValidationError("DOCX 包含不支持的 XML 内容")
                text, preferred_starts = _docx_text(archive, xml)
        except (BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, NotImplementedError, EOFError, OSError, zlib_error) as exc:
            raise ValidationError("无法读取 DOCX，请确认文件未损坏、未加密，或改用纯文本") from exc
    else:
        raise ValidationError("支持 TXT、Markdown 和 DOCX；暂不支持 PDF、旧版 DOC")
    if not text.strip():
        raise ValidationError("未读取到正文；扫描图片或图片型文档需先转为文字")
    if len(text) > MAX_CHARACTERS:
        raise ValidationError("参考正文不能超过 300 万字符")
    if "\x00" in text:
        raise ValidationError("参考文件包含无效文本字符")
    return {
        "name": name,
        "text": text,
        "analysis": analyze_script_structure(text, preferred_starts),
    }
