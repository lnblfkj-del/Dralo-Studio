"""Non-destructive screenplay preflight using the director's source parser."""

import math
import re

from app.core.errors import ValidationError
from app.services.screenplay_source_parser import VERSION, bracket_end, parse_sources
from app.services.screenplay_voice_sources import digest, physical_lines


def character_catalog(characters):
    return [
        {"asset_type": "character", "asset_id": f"story:{index}",
         "asset_name": item["name"], "aliases": item.get("aliases") or []}
        for index, item in enumerate(characters)
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
    ]


def spoken_char_count(text):
    # Count speech only; balanced performance notes do not contribute to voice duration.
    words, index = [], 0
    while index < len(text):
        if text[index] in "（(":
            end = bracket_end(text[index:])
            if end is not None:
                index += end + 1
                continue
        if text[index].isalnum():
            words.append(text[index])
        index += 1
    return len(words)


def review_sources(script, catalog, interpretations=None):
    positions = physical_lines(script)
    by_line = {row["line"]: row for row in positions}
    rows = [{"line": row["line"], "text": row["text"]} for row in positions if row["text"].strip()]
    errors, records = [], []
    try:
        records = parse_sources(rows, catalog, interpretations)
    except ValidationError as exc:
        line = exc.details["source_line"]
        errors.append({"line": line, "message": exc.message, **exc.details,
                       **by_line[line], "severity": "warning" if exc.details.get("resolvable") else "error"})
        # Do not restart parsing after an unknown cue: it may change subsequent
        # group/quote context. The remaining suffix is explicitly unassessed.
        records = parse_sources([row for row in rows if row["line"] < line], catalog, interpretations)
    for record in records:
        if record.get("speaker_kind") == "unbound":
            line = record["line"]
            errors.append({**by_line[line], "message": "该声音来源尚未确认，可保留正文并确认来源类型",
                           "source_line": line, "resolvable": True, "severity": "warning",
                           "failure_kind": "screenplay_source_ambiguous"})
    covered = {line for record in records for line in record["source_lines"]}
    unresolved = [row["line"] for row in rows if row["line"] not in covered]
    dialogue = [record for record in records if record["kind"] == "dialogue"]
    return {
        "version": VERSION, "script_fingerprint": digest(script),
        "status": "needs_review" if errors else "passed",
        "records": records, "errors": errors, "source_line_count": len(rows),
        "recognized_source_line_count": len(covered), "unresolved_lines": unresolved,
        "coverage_ratio": len(covered) / len(rows) if rows else 1.0,
        "dialogue_events": len(dialogue),
        "dialogue_chars": sum(spoken_char_count(row["spoken_text"]) for row in dialogue),
        "unbound_speakers": sorted({row["speaker"] for row in dialogue if row["speaker_kind"] == "unbound"}),
    }


def duration_estimate(review):
    if review["errors"]:
        return {"status": "unavailable", "reason": "正文尚有未识别范围，不能可靠估时"}
    chars = review["dialogue_chars"]
    return {
        "status": "estimate", "spoken_seconds_range": [math.ceil(chars / 5), math.ceil(chars / 3)],
        "speech_chars_per_second_range": [3, 5],
        "assumptions": "按中文每秒3至5字估算发声部分；合说计一次。动作、停顿及声音并行未定，非成片总时长。",
        "total_seconds": None,
    }


def canonical_format_warnings(script):
    """Equivalent legacy formatting remains readable; template drift is only a warning."""
    warnings = []
    for i, raw in enumerate(script.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if (not re.match(r"^[^：:\n]+：.+$", line) or line.startswith(("#", "【", "[", "（", "("))
                or (line.startswith(("场景", "场次")) and not re.match(r"^场景[0-9]+：", line))):
            warnings.append(i)
    return warnings
