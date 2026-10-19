"""Normalize a bounded set of V3 field placements using exact frozen sources."""

# ruff: noqa: RUF001

import re
from copy import deepcopy


def normalize_fields(document, sources):
    result = deepcopy(document)
    changes = []
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        return result, changes
    source_map = {source["source_segment_id"]: source for source in sources}
    for index, row in enumerate(result["assets"]):
        if not isinstance(row, dict):
            continue
        if not isinstance(row.get("kind"), str):
            continue
        occurrences = row.get("occurrences") if isinstance(row.get("occurrences"), list) else []
        necessity = row.get("necessity") or {}
        if row.get("kind") == "character" and row.get("visible_performer") is True and isinstance(necessity, dict) and isinstance(necessity.get("reasons"), list):
            reasons = necessity["reasons"]
            visible = any(isinstance(record, dict) and record.get("source_segment_id") in source_map
                          and record.get("evidence_text") and record["evidence_text"] in source_map[record["source_segment_id"]]["text"]
                          and row.get("name", "") in record["evidence_text"]
                          and re.match(r"^(?:人物|动作|画面)[：:]", record["evidence_text"].strip())
                          for record in occurrences)
            if "visible_performer" not in reasons and row.get("kind") == "character" and visible:
                necessity["reasons"] = [*reasons, "visible_performer"]
            if "visible_performer" not in necessity["reasons"]:
                continue
            row.pop("visible_performer")
            changes.append({"rule": "redundant_visible_performer.v1", "asset_index": index})
        for field in ("character_name", "base_scene_name", "owner"):
            if row.get("kind") not in {"character_name": {"character", "costume", "character_voice"},
                                       "base_scene_name": {"scene"}, "owner": {"prop"}}[field]:
                continue
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or not isinstance(row.get("facts", []), list):
                continue
            if field in {"character_name", "base_scene_name"} and not str(row.get("name", "")).startswith(value):
                continue
            facts = row.get("facts") or []
            if any(not isinstance(fact, dict) for fact in facts):
                continue
            # Existing incompatible facts must not be silently replaced.
            if any(f.get("field") == field and f.get("value") != value for f in facts if isinstance(f, dict)):
                continue
            records = []
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    continue
                source = source_map.get(occurrence.get("source_segment_id"))
                quote = occurrence.get("evidence_text") or ""
                if source and quote and value in quote and quote in source["text"]:
                    records.append(occurrence)
            if not records:
                continue
            record = records[0]
            if not any(f.get("field") == field and f.get("value") == value for f in facts):
                row["facts"] = [*facts, {"field": field, "value": value, "origin": "source",
                    "source_segment_id": record["source_segment_id"],
                    "source_unit_id": record.get("source_unit_id", ""), "evidence_text": record["evidence_text"]}]
            row.pop(field)
            changes.append({"rule": "grounded_top_level_fact.v1", "asset_index": index, "field": field})
        facts = row.get("facts")
        if isinstance(facts, list):
            empty = [f for f in facts if isinstance(f, dict) and f.get("field") == "group_size"
                     and f.get("value") is None and f.get("origin") == "proposed"
                     and not any(f.get(k) for k in ("source_segment_id", "source_unit_id", "evidence_text"))]
            if empty:
                row["facts"] = [f for f in facts if f not in empty]
                changes.append({"rule": "empty_group_count_placeholder.v1", "asset_index": index, "original": empty})
                if row.get("kind") == "character" and not row.get("character_form"):
                    labels = []
                    for label in row.get("aliases") or []:
                        if not isinstance(label, str):
                            continue
                        for occurrence in occurrences:
                            if not isinstance(occurrence, dict):
                                continue
                            source = source_map.get(occurrence.get("source_segment_id"))
                            quote = occurrence.get("evidence_text") or ""
                            if source and quote in source["text"] and re.search(
                                r"(?:数十|数百|数千|数万|多|众多)(?:名|位|个)?" + re.escape(label), quote
                            ):
                                labels.append(label)
                    if len(set(labels)) == 1:
                        row.update(character_form="crowd", group_label=labels[0], group_size=None)
                        changes.append({"rule": "explicit_source_crowd.v1", "asset_index": index, "label": labels[0]})
    return result, changes
