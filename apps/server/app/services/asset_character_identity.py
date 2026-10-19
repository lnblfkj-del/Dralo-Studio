"""Project confirmed character names, local labels and evidenced representations."""

# ruff: noqa: RUF001

import hashlib
import re
from collections import defaultdict
from copy import deepcopy

VERSION = "character-identity.ar1.v1"
GROUPS = {"crowd", "fixed_group"}


def key(value):
    return str(value or "").strip().casefold()


def scope_keys(row):
    return sorted({
        f"ep:{record['episode_number']}:source:{record.get('source_segment_id') or record.get('scene') or 'unspecified'}"
        + (f":unit:{record['source_unit_id']}" if record.get("source_unit_id") else "")
        for record in row.get("usage_records") or [] if record.get("episode_number") is not None
    } or {f"ep:{number}:unspecified" for number in row.get("episode_numbers") or []})


def _story_characters(context):
    return ((context.get("confirmed_story_bible") or {}).get("content") or {}).get("characters") or []


def _revisions(context):
    frozen = context.get("source_script_revisions") or {}
    return {str(row["episode_number"]): frozen[str(row["episode_id"])]
            for row in context.get("episodes") or []
            if row.get("episode_number") is not None and str(row.get("episode_id")) in frozen}


def _entity(row, roster):
    name = key(row.get("name"))
    matches = [person for person in roster if key(person.get("name")) == name]
    attrs = row.get("attributes") or {}
    if len(matches) == 1 and matches[0].get("character_id") and attrs.get("character_form", "single") not in GROUPS:
        return "story:" + str(matches[0]["character_id"])
    return "extraction:" + str(attrs.get("extraction_identity") or row.get("candidate_id") or name)


def _transition(base, variant, scripts, revisions):
    # Name morphology alone never proves identity. Require a transformation by
    # the named subject immediately followed by the new speaker in one scene.
    prefixes = {"魔化": r"魔化|异化", "兽化": r"兽化|异化"}
    prefix = variant.removesuffix(base) if variant.endswith(base) else ""
    if prefix not in prefixes:
        return None
    action = re.compile(r"^动作[：:]\s*(?:特写[—－-]*\s*)?" + re.escape(base)
                        + r"(?=.*(?:" + prefixes[prefix] + r"))"
                        + r"(?:整个人|全身|周身|猛然|突然|开始|逐渐|服下|吞下|变成|化为|异化|魔化|兽化)")
    cue = re.compile(r"^" + re.escape(variant) + r"[：:]")
    for number, script in sorted(scripts.items()):
        if str(number) not in revisions:
            continue
        lines = script.splitlines()
        for index, line in enumerate(lines):
            if not action.match(line.strip()):
                continue
            if re.search(r"看见|看到|目睹|想象|幻象|梦见|假扮|伪装|并未|没有|未曾|未能|试图|准备|即将|将要|可能|仿佛|似乎", line):
                continue
            for end in range(index + 1, min(index + 5, len(lines))):
                following = lines[end].strip()
                if re.match(r"^(?:场景|场次|人物)[\d一二三四五六七八九十：:]", following):
                    break
                if cue.match(following):
                    return {"episode_number": number, "script_revision": revisions[str(number)],
                            "line_start": index + 1, "line_end": end + 1,
                            "source_excerpt": "\n".join(lines[index:end + 1]),
                            "source_text_sha256": hashlib.sha256(script.encode()).hexdigest()}
                if following and not re.match(r"^(?:动作|音效|环境声|环境音|配乐BGM)[：:]", following):
                    break
    return None


def project_character_identities(candidates, *, context, existing, scripts=None, decisions=None):
    """Annotate V3 candidates in place; never merge, renumber or rewrite facts."""
    changed = False
    roster = _story_characters(context)
    characters = [row for row in candidates if row.get("requirement_type", row.get("asset_type")) == "character"
                  and (row.get("attributes") or {}).get("extraction_contract_version") == 3
                  and row.get("selected", True) and not row.get("merged_into_candidate_id")]
    names = defaultdict(list)
    for row in characters:
        names[key(row["name"])].append(row)
    base_names = dict(names)
    for row in existing:
        if row.get("asset_type") == "character" and key(row.get("name")) not in base_names:
            base_names[key(row["name"])] = [row]
    for person in roster:
        if key(person.get("name")) not in base_names:
            base_names[key(person.get("name"))] = [{"name": person["name"], "attributes": {
                "character_form": person.get("character_form") or "single"}}]
    canonical = {key(row.get("name")) for row in [*characters, *existing] if row.get("asset_type", "character") == "character"}
    canonical.update(key(person.get("name")) for person in roster)
    references = defaultdict(set)
    for person in roster:
        for alias in person.get("aliases") or []:
            references[key(alias)].add(key(person.get("name")))
    for row in existing:
        if row.get("asset_type") == "character":
            for alias in (row.get("attributes") or {}).get("aliases") or []:
                references[key(alias)].add(key(row.get("name")))
    revisions = _revisions(context)
    for row in characters:
        attrs = deepcopy(row.get("attributes") or {})
        decision = (decisions or {}).get(str(row.get("candidate_id"))) or {}
        if "aliases" in decision:
            attrs["confirmed_character_aliases"] = list(decision["aliases"])
        old_identity = attrs.get("character_identity") or {}
        if old_identity.get("decision_source") == "user":
            continue
        form = attrs.get("character_form") or "single"
        identity = {"version": VERSION, "entity_key": _entity(row, roster), "canonical_name": row["name"],
                    "representation_key": attrs.get("extraction_identity"), "form": form,
                    "scope": "source" if form in GROUPS else "project", "scope_keys": scope_keys(row)}
        if form == "single" and key(row["name"]) not in {key(p.get("name")) for p in roster}:
            options = []
            for base_name, owners in base_names.items():
                if len(owners) != 1 or base_name == key(row["name"]):
                    continue
                base = owners[0]
                if (base.get("attributes") or {}).get("character_form", "single") in GROUPS:
                    continue
                proof = _transition(base["name"], row["name"], scripts or {}, revisions)
                if proof:
                    options.append((base, proof))
            if len(options) == 1:
                base, proof = options[0]
                identity.update(entity_key=_entity(base, roster), canonical_name=base["name"],
                                relation="representation_of", base_candidate_id=base.get("candidate_id") or (
                                    old_identity.get("base_candidate_id") if old_identity.get("canonical_name") == base["name"] else None),
                                representation_label=row["name"], evidence=proof)
        original = {key(label): str(label).strip() for label in row.get("aliases") or [] if key(label)}
        original.update({key(claim["label"]): claim["label"] for claim in attrs.get("character_alias_claims") or []})
        if "aliases" in decision:
            original = {key(label): str(label).strip() for label in decision["aliases"] if key(label)}
        manual = {key(label) for label in attrs.get("confirmed_character_aliases") or []}
        claims, active = [], [row["name"]]
        for alias, label in sorted(original.items()):
            if alias == key(row["name"]):
                continue
            owners = references.get(alias, set())
            confirmed = alias in manual or owners == {key(row["name"])}
            status = "confirmed" if confirmed else "proposed"
            if form in GROUPS:
                status = "scoped"
            elif alias in canonical and not confirmed:
                status = "separate_representation" if identity.get("relation") else "reserved_name"
            elif len(owners) > 1:
                status = "ambiguous"
            if status == "confirmed":
                active.append(label)
            claims.append({"label": label, "status": status,
                           "scope_keys": identity["scope_keys"] if status == "scoped" else [],
                           "origin": "user" if alias in manual else "confirmed_reference" if confirmed else "extraction"})
        attrs["character_identity"] = identity
        attrs["character_alias_claims"] = claims
        changed = changed or attrs != row.get("attributes") or row.get("aliases") != list(dict.fromkeys(active))
        row["attributes"] = attrs
        row["aliases"] = list(dict.fromkeys(active))
        row["normalized_aliases"] = sorted({key(value) for value in row["aliases"]})
    return changed


async def refresh_character_identities(session, project_id, candidates, *, context, existing, decisions=None):
    from sqlalchemy import select

    from app.models import Episode

    revisions = _revisions(context)
    numbers = {int(k) for k in revisions}
    scripts = {}
    if numbers:
        episodes = await session.scalars(select(Episode).where(Episode.project_id == project_id, Episode.number.in_(numbers)))
        for episode in episodes:
            revision = revisions.get(str(episode.number))
            if revision == episode.script_revision:
                scripts[episode.number] = episode.script or ""
    return project_character_identities(candidates, context=context, existing=existing, scripts=scripts, decisions=decisions)
