"""Relate literal evidence within a frozen scene, without accepting model aliases as identity."""

# ruff: noqa: RUF001

import re

from app.services.asset_source_units import named_header_reference, unit_source
from app.services.screenplay_source_parser import SCENE, SCENE_SLUG, split_cue, unwrap


def scene_at(source, offset):
    """Return the actual header offset, not a model-provided scene label."""
    current = (-1, "")
    start = 0
    for line in source["text"].splitlines(keepends=True):
        if start > offset:
            break
        plain = unwrap(line.strip())
        cue = split_cue(plain)
        if SCENE_SLUG.fullmatch(plain) or (cue and SCENE.fullmatch(unwrap(cue[0]))):
            current = (start, plain)
        start += len(line)
    return current


def record_span(source, record):
    if record.source_unit_id:
        scoped = unit_source(source, record.source_unit_id)
        return scoped["start"] - source["start"], scoped["text"]
    quote = record.evidence_text
    if not quote.strip() or source["text"].count(quote) != 1:
        return None
    start = source["text"].index(quote)
    if scene_at(source, start) != scene_at(source, start + len(quote.rstrip()) - 1):
        return None
    return start, quote


def _mentions(name, text):
    # Latin identifiers require word boundaries; Chinese names use the complete name.
    expression = re.escape(name)
    if name.isascii():
        expression = r"(?<!\w)" + expression + r"(?!\w)"
    return bool(name and re.search(expression, text))


def _scene_matches(name, header):
    if named_header_reference("scene", name, header):
        return True
    cue = split_cue(unwrap(header.strip()))
    if not cue or not SCENE.fullmatch(unwrap(cue[0])):
        return False
    heading = [part.strip() for part in re.split(r"[，,]", cue[1])]
    parts = [part.strip() for part in name.split("·")]
    locations = {heading[0], *re.split(r"与|和|及|、", heading[0])}
    return (parts[0] in locations and all(part in {"日", "夜", "内景", "外景"}
                                        and part in heading[1:] for part in parts[1:]))


def _scene_v2(name, header):
    """Normalize explicit interior labels, never arbitrary location substrings."""
    from app.services.asset_identity_variants import scene_label, time_key

    cue = split_cue(unwrap(header.strip()))
    if not cue or not SCENE.fullmatch(unwrap(cue[0])):
        return False
    heading = [part.strip() for part in re.split(r"[，,]", cue[1])]
    name, time = scene_label(name)
    parts = [part.strip() for part in name.split("·")]
    if time:
        parts.append(time)
    # A sentence terminator in a scene heading is not part of the place name.
    base = heading[0].removesuffix("。").strip()
    tags = {time_key(part) for part in heading[1:]}
    for suffix, tag in [("屋内", "内景"), ("室内", "内景"), ("室外", "外景")]:
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            tags.add(tag)
            break
    return parts[0] in {base, *re.split(r"与|和|及|、", base)} and all(
        time_key(part) in {"日", "夜", "内景", "外景"} and time_key(part) in tags for part in parts[1:])


def _composite_context(asset, source, span, evidence, header):
    # A grouped production prop is not an assertion that its members are aliases
    # or mechanically connected. Every member must have literal local evidence.
    parts = [part.strip() for part in re.split(r"与|及|、", asset.name)]
    if asset.kind != "prop" or not 2 <= len(parts) <= 4 or len(set(parts)) != len(parts):
        return False
    if abs(span[0] - evidence[0]) > 1200:
        return False
    texts = [span[1], evidence[1]]
    members = []
    for part in parts:
        options = [part] if any(_mentions(part, text) for text in texts) else []
        if not options:
            # Only a location explicitly present in the actual scene heading may
            # be removed from the front of a component's display name.
            options = [part[index:] for index in range(2, len(part) - 3)
                       if part[:index] in header and any(_mentions(part[index:], text) for text in texts)]
        if not options:
            return False
        members.append(max(options, key=len))
    return (len(set(members)) == len(parts)
            and all(any(_mentions(member, text) for member in members) for text in texts))


def necessity_relation(asset, source, *, version="scene_entity.v1"):
    evidence = record_span(source, asset.necessity)
    if evidence is None:
        return "unresolved"
    evidence_scene = scene_at(source, evidence[0])
    for occurrence in asset.occurrences:
        if occurrence.source_segment_id != asset.necessity.source_segment_id:
            continue
        span = record_span(source, occurrence)
        if span is None:
            continue
        if asset.kind == "character" and _mentions(asset.name, evidence[1]) and _mentions(asset.name, span[1]):
            return "named_entity"
        if scene_at(source, span[0]) != evidence_scene:
            continue
        if ((occurrence.source_unit_id and occurrence.source_unit_id == asset.necessity.source_unit_id)
                or occurrence.evidence_text == asset.necessity.evidence_text):
            return "same_unit"
        scene_matcher = _scene_v2 if version == "scene_entity.v2" else _scene_matches
        if asset.kind == "scene" and evidence_scene[0] >= 0 and scene_matcher(asset.name, evidence_scene[1]):
            return "scene_header"
        if (version == "scene_entity.v2" and evidence_scene[0] >= 0
                and _composite_context(asset, source, span, evidence, evidence_scene[1])):
            return "composite_context"
        if _mentions(asset.name, evidence[1]) and _mentions(asset.name, span[1]):
            return "named_entity"
    # A possible relationship is reviewable; being in the same episode alone is not proof.
    return "unresolved"
