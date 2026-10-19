"""Project confirmed voice text onto actual speakers without another model call."""


from copy import deepcopy

from sqlalchemy import select

from app.core.errors import ValidationError
from app.models import Job
from app.services.asset_necessity_service import validate_policy
from app.services.creation_breakdown_planning import normalized_asset_key
from app.services.director_output_transport import digest
from app.services.screenplay_source_parser import classify, identity_index, parse_sources


def _speech_usages(source, catalog, identities):
    rows, offsets = [], {}
    offset = source["start"]
    for number, raw in enumerate(source["text"].splitlines(keepends=True), source.get("line_start", 1)):
        line = raw.rstrip("\r\n")
        rows.append({"line": number, "text": line})
        offsets[number] = (offset, offset + len(line))
        offset += len(raw)
    try:
        records = parse_sources(rows, catalog)
    except ValidationError:
        # An ambiguous source cannot establish a group; retain only explicit known cues.
        records = []
        for row in rows:
            try:
                record = classify(row["text"], identities, set(), row["line"])
            except ValidationError:
                continue
            records.append({**record, "source_lines": [row["line"]]})
    for record in records:
        if record.get("speaker_kind") not in {"character", "group"} or not record.get("spoken_text"):
            continue
        lines = record["source_lines"]
        start, end = offsets[lines[0]][0], offsets[lines[-1]][1]
        quote = source["text"][start - source["start"]:end - source["start"]]
        usage = {
            "episode_number": source["episode_number"], "source_segment_id": source["source_segment_id"],
            "source_excerpt": quote, "source_text_sha256": source["text_sha256"],
            "script_revision": source["script_revision"], "alignment_status": "exact", "needs_review": False,
            "start": start, "end": end, "line_start": lines[0], "line_end": lines[-1],
            "scene": "", "performance": "", "timing": "",
        }
        yield record.get("speaker_refs") or [], usage


def project_voice_profiles(breakdown, *, context, sources, existing, policy, kinds):
    """Only confirmed descriptions and unambiguous, frozen speech create profiles."""
    validate_policy(policy)
    result = deepcopy(breakdown)
    if not policy.get("stable_voice_profiles") or "character_voice" not in kinds:
        return result
    bible = context.get("confirmed_story_bible") or {}
    if bible.get("status") != "confirmed":
        return result
    confirmed = (bible.get("content") or {}).get("characters") or []
    characters = list(result.get("characters") or [])
    keys = {row["attributes"]["asset_key"] for row in characters}
    characters += [{"name": row["name"], "aliases": (row.get("attributes") or {}).get("aliases") or [],
                    "attributes": row.get("attributes") or {}}
                   for row in existing if row.get("asset_type") == "character"
                   and (row.get("attributes") or {}).get("asset_key") not in keys]
    catalog = [{"asset_type": "character", "asset_id": index, "asset_name": row["name"],
                "aliases": row.get("aliases") or []} for index, row in enumerate(characters)]
    identities, _ = identity_index(catalog)
    usages = {}
    for source in sources:
        for speaker_refs, usage in _speech_usages(source, catalog, identities):
            for index in speaker_refs:
                usages.setdefault(index, []).append(usage)
    voices = result.setdefault("character_voices", [])
    for index, character in enumerate(characters):
        attrs = character["attributes"]
        key = attrs.get("asset_key")
        if attrs.get("character_form") in {"fixed_group", "crowd"}:
            continue
        matches = [row for row in confirmed if isinstance(row, dict)
                   and str(row.get("name") or "").strip().casefold() == character["name"].strip().casefold()]
        if not key or not usages.get(index) or len(matches) != 1:
            continue
        reference = matches[0]
        if reference.get("importance") == "functional" and "character_voice" not in policy["independent_audio_types"]:
            continue
        voice = reference.get("voice")
        if not isinstance(voice, str) or not voice.strip():
            continue
        voice = voice.strip()
        if attrs.get("voice") and attrs["voice"] != voice:
            continue
        provenance = {"kind": "confirmed_story_bible", "artifact_id": bible.get("artifact_id"),
                      "version": bible.get("version"), "revision": bible.get("revision"),
                      "character_name": reference["name"], "field": "voice", "value": voice,
                      "content_sha256": digest(bible["content"])}
        # The character fallback is used even when native video needs no TTS reference.
        if not attrs.get("voice"):
            attrs.update(voice=voice, voice_profile_source=provenance)
        if any((row.get("attributes") or {}).get("linked_asset_key") == key
               or str((row.get("attributes") or {}).get("character_name") or "").casefold()
               == character["name"].casefold() for row in voices):
            continue
        records = sorted(usages[index], key=lambda row: (row["episode_number"], row["start"]))
        first = records[0]
        voices.append({
            "name": character["name"] + "声线", "aliases": [], "description": voice, "prompt_anchor": voice,
            "episode_numbers": sorted({row["episode_number"] for row in records}), "usage_records": records,
            "attributes": {
                "asset_key": normalized_asset_key("character_voice", key), "linked_asset_key": key,
                "character_name": character["name"], "voice": voice, "voice_profile_source": provenance,
                "audio_purpose": "character_voice", "extraction_contract_version": 3,
                "voice_profile_only": True, "media_delivery": "description", "needs_review": False,
                "facts": [], "necessity_records": [{
                    "reasons": ["fixed_voice"], "source_segment_id": first["source_segment_id"],
                    "evidence_text": first["source_excerpt"], "rationale": "沿用已确认角色声线，供跨片段一致性控制",
                    "user_requirement_id": "", "policy_version": policy["version"],
                    "policy_sha256": policy["sha256"], "source_verified": True,
                    "profile_source": provenance,
                }],
            },
        })
    return result


async def add_frozen_voice_profiles(session, job, breakdown, context, existing, progress):
    policy = progress.get("asset_necessity_policy") or (job.payload.get("parameters") or {}).get("asset_necessity_policy")
    if not policy or not policy.get("stable_voice_profiles"):
        return breakdown
    kinds = progress.get("requirement_types") or (job.payload.get("parameters") or {}).get("requirement_types") or []
    if "character_voice" not in kinds:
        return breakdown
    bible = context.get("confirmed_story_bible") or {}
    if bible.get("status") != "confirmed" or not any(
        isinstance(row, dict) and row.get("voice") for row in (bible.get("content") or {}).get("characters") or []
    ):
        return breakdown
    payloads = [job.payload]
    if job.parent_job_id:
        payloads = list(await session.scalars(select(Job.payload).where(Job.parent_job_id == job.parent_job_id)))
    scopes = set((progress.get("batches") or {}).keys())
    sources = {}
    for payload in payloads:
        parameters = payload.get("parameters") or {}
        if scopes and parameters.get("scope_key") not in scopes:
            continue
        for source in parameters.get("source_segments") or []:
            sources[source["source_segment_id"]] = source
    projected = project_voice_profiles(breakdown, context=context, sources=list(sources.values()),
                                       existing=existing, policy=policy, kinds=kinds)
    # Character keys are already canonical; voice identity must be canonicalized too.
    from app.services.asset_extraction_registry import project_registry
    return project_registry(projected, context=context, existing=existing)
