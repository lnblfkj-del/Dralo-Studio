"""Keep model-proposed audio annotations out of visual asset matching."""

import re

from app.services.segment_script_semantics import _audio_entries


def split_reference_names(names, shot, catalog):
    known = {str(name).strip().casefold() for asset in catalog
             for name in [asset.get("asset_name"), *(asset.get("aliases") or [])] if name}
    audio = _audio_entries(shot.shot_id, shot.audio_note, preserve_lines=True)
    visual = "\n".join([shot.subject, shot.action]).casefold()
    pending, annotations = [], []
    for name in dict.fromkeys(names):
        key = name.strip().casefold()
        in_audio = any(key and key in row["text"].casefold()
                       for rows in audio.values() for row in rows)
        sound_name = re.search(r"(?:声|声响|声音|音效|sound|sounds|noise|ambience)$", key)
        music_only = key not in visual and any(key and key in row["text"].casefold()
                                               for row in audio["music"])
        if key not in known and key not in shot.subject.casefold() and in_audio and (sound_name or music_only):
            annotations.append(name)
        else:
            pending.append(name)
    return pending, annotations


def append_asset_review(proposal, sources, shots, catalog, *, shot_id_map=None):
    mapping = shot_id_map or {}
    by_id = {mapping.get(shot.shot_id, shot.shot_id): shot for shot in shots}
    for source in sources:
        pending, audio = split_reference_names(source["unresolved_names"], by_id[source["shot_id"]], catalog)
        source["unresolved_names"] = pending
        if audio:
            source["audio_reference_notes"] = audio
        if not pending:
            continue
        proposal["continuity_report"]["status"] = "blocked"
        proposal["continuity_report"]["issues"].append({
            "code": "unmatched_asset", "shot_id": source["shot_id"],
            "message": "资产匹配待确认：" + "、".join(pending),
        })
        for segment in proposal["segments"]:
            if source["shot_id"] in segment["shot_ids"]:
                refs = segment["refs"]
                refs["unmatched_assets"] = list(dict.fromkeys([*refs.get("unmatched_assets", []), *pending]))
