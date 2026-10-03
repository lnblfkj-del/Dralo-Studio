"""Bounded, explicitly scoped planning context, never inferred character presence."""

from copy import deepcopy

from app.core.errors import ConflictError

OVERVIEW = ("title", "logline", "genre", "tone", "audience", "world", "themes")


def span_of(event):
    span = event.get("episode_range")
    if span:
        return span["start"], span["end"]
    number = event.get("episode_hint")
    return (number, number) if number else None


def story_context(story, start, end, *, character_ids=None, names=None):
    ids, names = set(character_ids or []), set(names or [])
    events = []
    for event in story.get("event_timeline", []):
        span = span_of(event)
        related = bool(ids.intersection(event.get("character_ids", [])))
        if character_ids is not None:
            if related:
                events.append(event)
        elif span is None or not (span[1] < start or span[0] > end):
            events.append(event)
    for event in events:
        ids.update(event.get("character_ids", []))
    characters = []
    for person in story.get("characters", []):
        spans = person.get("appearance_ranges", [])
        in_range = any(s["start"] <= end and s["end"] >= start for s in spans)
        if (
            person.get("character_id") in ids
            or person.get("name") in names
            or (character_ids is None and (in_range or person.get("importance") == "core"))
        ):
            characters.append(person)
    if not characters and not ids and not names:
        characters = story.get("characters", [])
    return {
        **{key: deepcopy(story.get(key)) for key in OVERVIEW},
        "characters": deepcopy(characters),
        "event_timeline": deepcopy(events),
        "phase_plan": [
            deepcopy(phase)
            for phase in story.get("phase_plan", [])
            if (span := span_of(phase)) and span[0] <= end and span[1] >= start
        ],
        "coverage": {"start": start, "end": end, "kind": "planning_not_actual_facts"},
    }


def checked_prompt(prompt):
    if len(prompt) > 120000:
        raise ConflictError(
            "本步骤必要上下文超过本地安全容量；已保存成功步骤，不能截断关键资料继续生成。请缩小当前资料范围。"
        )
    return prompt
