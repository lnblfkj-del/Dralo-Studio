"""Ground standalone speech in complete screenplay lines, not model descriptions."""

from app.core.errors import ValidationError
from app.services.screenplay_source_parser import classify, identity_index


def standalone_voice_source(attributes, usages, sources, visual_keys):
    name = attributes.get("character_name")
    if not name or attributes.get("needs_review"):
        return None
    identities, _ = identity_index(
        [
            {
                "asset_type": "character",
                "asset_name": row.get("name"),
                "aliases": row.get("aliases", []),
            }
            for row in visual_keys
            if row.get("requirement_type") == "character"
        ]
    )
    kinds = set()
    for usage in usages:
        if usage.get("alignment_status") != "exact":
            return None
        source = sources[usage["source_segment_id"]]
        start = usage["start"] - source["start"]
        end = usage["end"] - source["start"]
        # A quoted substring inside an action or another speaker's line is not a cue.
        offset, matched = 0, False
        for raw in source["text"].splitlines(keepends=True):
            line = raw.rstrip("\r\n")
            line_end = offset + len(line)
            if start <= offset and line_end <= end:
                try:
                    record = classify(line, identities, set(), 1)
                except ValidationError:
                    record = {}
                if record.get("speaker") == name:
                    kind = record.get("speaker_kind")
                    if kind not in {"device", "narration"} or not record.get("spoken_text"):
                        return None
                    kinds.add(kind)
                    matched = True
            offset += len(raw)
        if not matched:
            return None
    if len(kinds) != 1:
        return None
    return {"speaker_kind": kinds.pop(), "speaker": name, "grounding": "source_cue"}


def is_standalone_extraction_voice(attributes):
    voice = attributes.get("voice_source") or {}
    if (isinstance(voice, dict) and voice.get("grounding") == "user_independent"
            and attributes.get("identity_resolution_by") == "user_independent"
            and any(row.get("keep_independent") for row in attributes.get("manual_production_reviews", []))
            and not attributes.get("linked_asset_key")):
        return True
    return (
        attributes.get("extraction_contract_version") in {2, 3}
        and isinstance(voice, dict)
        and voice.get("grounding") == "source_cue"
        and voice.get("speaker_kind") in {"device", "narration"}
        and voice.get("speaker") == attributes.get("character_name")
        and (
            not attributes.get("linked_asset_key")
            or (
                voice.get("speaker_kind") == "device"
                and voice.get("linked_device_asset_key") == attributes.get("linked_asset_key")
            )
        )
    )
