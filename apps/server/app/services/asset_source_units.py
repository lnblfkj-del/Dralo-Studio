"""Frozen source pointers: the server supplies text, the model selects a unit."""

import hashlib
import re
from copy import deepcopy

from app.core.errors import ModelOutputBusinessValidationError
from app.services.screenplay_source_parser import SCENE, split_cue, unwrap

VERSION = "asset-source-unit.v1"
MAX_UNIT_CHARS = 1000


def build_units(source: dict) -> list[dict]:
    units = []
    offset = 0
    for line in source["text"].splitlines(keepends=True):
        for cut in range(0, len(line), MAX_UNIT_CHARS):
            start = offset + cut
            end = min(offset + len(line), start + MAX_UNIT_CHARS)
            text = source["text"][start:end]
            digest = hashlib.sha256(f'{source["source_segment_id"]}:{start}:{end}:{text}'.encode()).hexdigest()
            units.append({
                "source_unit_id": f"u{len(units) + 1:04d}-{digest[:12]}",
                "start": source["start"] + start, "end": source["start"] + end,
                "line_start": source["line_start"] + source["text"].count("\n", 0, start),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            })
        offset += len(line)
    return units


def validate_units(source: dict) -> None:
    if "source_units" not in source and "source_unit_version" not in source:
        return
    if source.get("source_unit_version") != VERSION or source.get("source_units") != build_units(source):
        raise ModelOutputBusinessValidationError("原文单元索引与冻结正文不一致, 已停止处理")


def unit_source(source: dict, unit_id: str) -> dict:
    unit = next((row for row in source.get("source_units") or [] if row["source_unit_id"] == unit_id), None)
    if unit is None:
        raise ModelOutputBusinessValidationError("原文单元编号不属于该冻结段, 未采用其他段或模糊匹配")
    start, end = unit["start"] - source["start"], unit["end"] - source["start"]
    return {**source, "text": source["text"][start:end], "start": unit["start"],
            "end": unit["end"], "line_start": unit["line_start"]}


def prompt_units(source: dict) -> list[dict]:
    result = []
    for unit in source["source_units"]:
        text = unit_source(source, unit["source_unit_id"])["text"]
        cue = split_cue(unwrap(text.strip()))
        result.append({"source_unit_id": unit["source_unit_id"], "text": text,
                       "action_review": bool(cue and unwrap(cue[0]) in {"动作", "画面"})})
    return result


def named_header_reference(kind: str, name: str, text: str) -> bool:
    cue = split_cue(unwrap(text.strip()))
    if not cue:
        return False
    label = unwrap(cue[0])
    if kind == "character" and label in {"人物", "角色", "出场人物"}:
        return name in {part.strip() for part in re.split(r"[\u3001\uff0c,;/]", cue[1])}
    if kind == "scene" and SCENE.fullmatch(label):
        from app.services.asset_identity_variants import scene_label
        location = re.split(r"[\uff0c,]", cue[1], maxsplit=1)[0].strip()
        return scene_label(name)[0] in {part.strip() for part in re.split(r"与|和|及|、", location)}
    return False


def resolve_records(document, sources: dict) -> tuple[dict, list[dict]]:
    result = deepcopy(document)
    changes = []
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        return result, changes
    for index, asset in enumerate(result["assets"]):
        if not isinstance(asset, dict):
            continue
        records = [(f"{kind}.{position}", row) for kind in ("occurrences", "facts")
                   if isinstance(asset.get(kind), list) for position, row in enumerate(asset[kind])]
        records.append(("necessity", asset.get("necessity")))
        for location, record in records:
            if not isinstance(record, dict) or not record.get("source_unit_id"):
                continue
            source = sources.get(record.get("source_segment_id"))
            if source is None:
                raise ModelOutputBusinessValidationError("原文单元引用了输入范围外的冻结段, 未写入资产")
            if location.startswith("facts.") and record.get("origin") != "source":
                raise ModelOutputBusinessValidationError("身份参考或模型建议不能冒用原文单元作为事实依据")
            scoped = unit_source(source, record["source_unit_id"])
            literal = scoped["text"]
            quote = record.get("evidence_text")
            if quote not in (None, "") and (not isinstance(quote, str) or not quote.strip() or quote not in literal):
                raise ModelOutputBusinessValidationError("原文单元与提供的引文不一致, 未自动纠正或替换引文")
            if not literal.strip():
                raise ModelOutputBusinessValidationError("空白原文单元不能作为出场或事实依据")
            record["evidence_text"] = quote if quote else literal
            changes.append({"rule": VERSION, "field": f"assets.{index}.{location}",
                            "source_segment_id": source["source_segment_id"],
                            "source_unit_id": record["source_unit_id"]})
    return result, changes
