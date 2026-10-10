"""Freeze recognition before submission; historical jobs keep their old parser."""

import json
import re
from copy import deepcopy
from hashlib import sha256

from app.core.errors import ValidationError
from app.services.screenplay_source_parser import VERSION, identity_index, name_and_directions, parse_sources, split_cue, unwrap

KEY = "screenplay_sources"


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def freeze_sources(snapshot):
    rows = snapshot.get("source_lines") or []
    if (any(not isinstance(r, dict) or type(r.get("line")) is not int or r["line"] <= 0
            or not isinstance(r.get("text"), str) or not r["text"].strip() for r in rows)
            or [r["line"] for r in rows] != sorted({r["line"] for r in rows})):
        raise ValidationError("正文来源行号必须唯一且有序，不能省略或编造来源")
    catalog = deepcopy(snapshot.get("asset_catalog") or [])
    known, _ = identity_index(catalog)
    # Hand-authored shot dialogue is also explicit identity evidence, not a new asset.
    for shot in snapshot.get("shots") or []:
        for line in str(shot.get("dialogue") or "").splitlines():
            cue = split_cue(unwrap(line.strip()))
            if cue:
                name = name_and_directions(cue[0])[0]
                parts = re.split(r"[/／、与和]", name)
                if len(parts) > 1 and all(part.strip().casefold() in known for part in parts):
                    continue
                labels = re.split(r"[/／、]", name) if name.casefold() not in known else [name]
                for label in labels:
                    label = label.strip()
                    if label and label.casefold() not in known and label not in {"两人", "二人", "两位"}:
                        identity = f"authored:{label.casefold()}"
                        catalog.append({"asset_id": identity, "asset_type": "character", "asset_name": label})
                        known[label.casefold()] = {identity}
    records = parse_sources(rows, catalog)
    return {**snapshot, KEY: {"version": VERSION, "source_digest": digest(rows),
                             "records": records, "records_digest": digest(records)}}


def frozen_records(snapshot):
    frozen = snapshot[KEY]
    if (not isinstance(frozen, dict) or frozen.get("version") != VERSION
            or frozen.get("source_digest") != digest(snapshot.get("source_lines") or [])
            or frozen.get("records_digest") != digest(frozen.get("records"))):
        raise ValidationError("正文识别规则或来源快照已变化，不能使用不同规则恢复旧任务")
    return deepcopy(frozen["records"])
