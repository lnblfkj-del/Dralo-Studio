"""Story proposal boundaries independent of model compliance."""

from copy import deepcopy

from app.core.errors import ConflictError, ValidationError
from app.schemas.story_planning import CharacterBatchCompletionRequest


OVERVIEW_FIELDS = ("title", "logline", "genre", "tone", "audience", "world", "themes")


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def _batch_character_key(person, index):
    character_id = str(person.get("character_id") or "").strip()
    return f"id:{character_id}" if character_id else f"legacy:{index}:{str(person.get('name') or '').strip()}"


def batch_completion_scope(request, source, content):
    request = CharacterBatchCompletionRequest.model_validate(request)
    if source.get("id") != request.artifact_id or source.get("revision") != request.expected_revision:
        raise ConflictError("故事设定已变化，请基于最新版本选择批量补全角色")
    characters = content.get("characters", [])
    targets = []
    for target in request.targets:
        if target.character_index >= len(characters):
            raise ValidationError("批量补全包含不存在的角色")
        person = characters[target.character_index]
        if target.character_id and target.character_id != person.get("character_id"):
            raise ConflictError("角色身份已变化，请刷新后重新选择")
        if any(not missing(person.get(field)) for field in target.fields):
            raise ConflictError("批量补全只处理空字段，已填写资料不会被覆盖")
        targets.append({
            **target.model_dump(),
            "character_key": _batch_character_key(person, target.character_index),
            "name": str(person.get("name") or f"角色 {target.character_index + 1}"),
        })
    return {
        "artifact_id": request.artifact_id,
        "expected_revision": request.expected_revision,
        "targets": targets,
        "story_snapshot": deepcopy(content),
    }


def restrict_batch_completion(scope, proposed, selected_character_keys=None, *, allow_empty=False):
    """Apply only authorized empty fields and report partial model output per role."""
    if not isinstance(proposed, dict):
        raise ValidationError("批量角色补全必须返回故事设定提案")
    before = deepcopy(scope["story_snapshot"])
    candidates = proposed.get("characters", [])
    if not isinstance(candidates, list):
        raise ValidationError("批量角色补全提案缺少 characters 数组")
    available_keys = {target["character_key"] for target in scope["targets"]}
    selected = available_keys if selected_character_keys is None else set(selected_character_keys)
    if not selected or not selected.issubset(available_keys):
        raise ValidationError("批量审核角色选择无效，请刷新后重试")
    report = []
    total_changes = 0
    for target in scope["targets"]:
        key = target["character_key"]
        person = before["characters"][target["character_index"]]
        matches = [row for row in candidates if _same_character(person, row)]
        if key not in selected:
            report.append({"character_key": key, "name": target["name"], "status": "excluded", "changed_fields": [], "missing_fields": target["fields"]})
            continue
        if len(matches) != 1:
            report.append({"character_key": key, "name": target["name"], "status": "missing" if not matches else "invalid", "changed_fields": [], "missing_fields": target["fields"]})
            continue
        changed_fields = []
        missing_fields = []
        for field in target["fields"]:
            value = matches[0].get(field)
            if missing(person.get(field)) and isinstance(value, str) and value.strip():
                person[field] = value.strip()
                changed_fields.append(field)
            else:
                missing_fields.append(field)
        total_changes += len(changed_fields)
        report.append({
            "character_key": key,
            "name": target["name"],
            "status": "complete" if not missing_fields else "partial" if changed_fields else "missing",
            "changed_fields": changed_fields,
            "missing_fields": missing_fields,
        })
    if total_changes == 0 and not allow_empty:
        raise ValidationError("批量角色补全没有返回任何可审核的有效内容")
    return before, report


def _same_character(before, candidate):
    if before.get("character_id"):
        return candidate.get("character_id") == before["character_id"]
    return candidate.get("name") == before.get("name")


def preserve_story_fields(before, proposed):
    """Keep existing extension data when a general Agent omits it."""
    result = {**deepcopy(before), **deepcopy(proposed)}
    old_people = before.get("characters", [])
    for index, person in enumerate(result.get("characters", [])):
        matches = [row for row in old_people if (
            row.get("character_id") == person["character_id"] if person.get("character_id")
            else row.get("name") == person.get("name")
        )]
        if len(matches) == 1:
            result["characters"][index] = {**deepcopy(matches[0]), **person}
    return result


def story_section_scope(request, source, content, section):
    if source.get("id") != request.get("artifact_id") or source.get("revision") != request.get("expected_revision"):
        raise ConflictError("故事设定已变化，请基于最新版本重新生成调整提案")
    return {
        "section": section,
        "artifact_id": request["artifact_id"],
        "expected_revision": request["expected_revision"],
        "story_snapshot": deepcopy(content),
    }


def restrict_story_section(scope, proposed, settings):
    """Restore every field outside the explicitly selected Story Bible section."""
    if not isinstance(proposed, dict):
        raise ValidationError("故事资料调整必须返回故事设定提案")
    before = deepcopy(scope["story_snapshot"])
    if scope["section"] == "overview":
        for field in OVERVIEW_FIELDS:
            if field in proposed:
                before[field] = deepcopy(proposed[field])
        return before
    if scope["section"] == "events":
        if not isinstance(proposed.get("event_timeline"), list):
            raise ValidationError("事件脉络调整必须返回 event_timeline 数组")
        before["event_timeline"] = deepcopy(proposed["event_timeline"])
        from app.services.story_bible_structure_review import validate_story_bible_structure
        validate_story_bible_structure(before, settings)
        return before
    raise ValidationError("故事资料调整范围无效")
