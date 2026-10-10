"""Render already-owned dialogue without reading performance directions aloud."""

from app.core.errors import ValidationError
from app.services.screenplay_source_parser import split_cue, spoken_parts, unwrap

LAYOUT = "source_lines.v2"


def dialogue_units(value):
    from app.services.segment_script_semantics import quoted_dialogue_closed

    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    units, index = [], 0
    while index < len(lines):
        source = [lines[index]]
        cue = split_cue(unwrap(lines[index]))
        if not cue:
            raise ValidationError("冻结台词缺少明确说话人，不能丢弃该句")
        speaker, directions, spoken = spoken_parts(*cue)
        index += 1
        if not spoken and index < len(lines) and lines[index].startswith(('“', '"')):
            source.append(lines[index])
            spoken = lines[index]
            index += 1
        while not quoted_dialogue_closed(spoken) and index < len(lines):
            source.append(lines[index])
            spoken += "\n" + lines[index]
            index += 1
        if not speaker or not spoken or not quoted_dialogue_closed(spoken):
            raise ValidationError("冻结台词为空或未闭合，不能猜测说话内容")
        units.append({"source_text": "\n".join(source), "speaker": speaker,
                      "text": spoken, "stage_directions": directions})
    return units
