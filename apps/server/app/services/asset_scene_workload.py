"""Bound output-risk work without dropping text or making a second model call."""

from app.services.screenplay_source_parser import ACTION, SCENE, SCENE_SLUG, split_cue, unwrap


def scene_starts(script):
    result = []
    offset = 0
    for line in script.splitlines(keepends=True):
        text = unwrap(line.strip())
        cue = split_cue(text)
        if SCENE_SLUG.fullmatch(text) or (cue and SCENE.fullmatch(unwrap(cue[0]))):
            result.append(offset)
        offset += len(line)
    return result


def output_load(script):
    # A risk estimate, not a tokenizer or proof of complete asset coverage.
    total = len(script) // 3
    for line in script.splitlines():
        if not line.strip():
            continue
        cue = split_cue(unwrap(line.strip()))
        total += 32
        if cue and unwrap(cue[0]) in ACTION:
            total += 80 + 24 * sum(line.count(mark) for mark in "、，；")
    return total


def workload_ranges(script, max_chars, output_tokens):
    from app.services.creation_breakdown_planning import script_chunk_ranges
    limit = max(128, int(output_tokens * 0.7))
    scenes = scene_starts(script)
    ranges = []
    start = 0
    while start < len(script):
        end = min(len(script), start + max_chars)
        if output_load(script[start:end]) > limit:
            lo, hi = start + 1, end
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if output_load(script[start:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            end = lo
        if end < len(script):
            # Prefer a complete scene; avoid creating a near-empty chunk.
            candidates = [pos for pos in scenes if start + (end - start) // 3 < pos <= end]
            if candidates:
                end = candidates[-1]
            else:
                end = start + script_chunk_ranges(script[start:], max_chars=end - start)[0][1]
        ranges.append((start, end))
        start = end
    return ranges
