"""M3 创作产物落库逻辑：Worker 完成后的统一入口。

本模块处理 Job 完成后的结构化结果解析、Artifact 创建和状态推进。
"""

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import (
    CreationSession,
    Job,
)
from app.services.creation_session_service import (
    CREATION_ARTIFACT_TYPES,
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
)


def _repair_creative_direction_understanding(
    payload: Any,
    input_snapshot: dict[str, Any],
    repaired_fields: list[str],
) -> Any:
    """Repair only an omitted tone using the user's frozen understanding."""
    if not isinstance(payload, dict):
        return payload
    understanding = payload.get("understanding")
    if not isinstance(understanding, dict):
        return payload
    if str(understanding.get("tone") or "").strip():
        return payload
    understanding["tone"] = str(input_snapshot.get("tone") or "").strip() or "未明确"
    repaired_fields.append("understanding.tone")
    return payload


def _candidate_aliases(candidate: dict[str, Any]) -> set[str]:
    return {
        str(value).strip().casefold()
        for value in [candidate.get("name", ""), *(candidate.get("aliases") or [])]
        if str(value).strip()
    }


def _candidate_identity_keys(candidate: dict[str, Any]) -> set[str]:
    """Return deterministic identities that are stronger than display names."""
    requirement_type = str(
        candidate.get("requirement_type") or candidate.get("asset_type") or ""
    )
    attributes = dict(candidate.get("attributes") or {})
    keys: set[str] = set()
    if requirement_type != "character_voice":
        asset_key = str(attributes.get("asset_key") or "").strip().casefold()
        if requirement_type != "character" and asset_key.startswith(f"{requirement_type}:") and asset_key.split(":", 1)[1].strip():
            keys.add(asset_key)
        return keys
    linked_asset_key = str(attributes.get("linked_asset_key") or "").strip().casefold()
    if linked_asset_key.startswith("character:"):
        keys.add(linked_asset_key)
    character_name = str(attributes.get("character_name") or "").strip().casefold()
    if character_name:
        keys.add(f"character:{character_name}")
    return keys


def _candidates_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("asset_type") != right.get("asset_type"):
        return False
    if (left.get("requirement_type") or left.get("asset_type")) != (
        right.get("requirement_type") or right.get("asset_type")
    ):
        return False
    left_identity, right_identity = _candidate_identity_keys(left), _candidate_identity_keys(right)
    if left_identity.intersection(right_identity):
        return True
    if left.get("requirement_type") == "character_voice" and left_identity and right_identity:
        # Shared nicknames such as "老板" must not merge different speakers.
        return False
    requirement_type = str(
        left.get("requirement_type") or left.get("asset_type") or ""
    )
    if requirement_type == "character":
        # Character aliases are descriptive input, not a durable identity.  A
        # single polluted alias must never collapse two named characters.
        return str(left.get("name") or "").strip().casefold() == str(
            right.get("name") or ""
        ).strip().casefold()
    return bool(_candidate_aliases(left).intersection(_candidate_aliases(right)))


def _candidate_requires_review(candidate: dict[str, Any]) -> bool:
    return bool(
        dict(candidate.get("attributes") or {}).get("needs_review")
        or any(record.get("needs_review") for record in candidate.get("usage_records") or [])
        or not str(candidate.get("prompt_anchor") or "").strip()
    )


_REMAINS_MARKER_RE = re.compile(r"遗骨|遗骸|骸骨|白骨|遗体|尸骨|骨骼")
_CHARACTER_ROLE_VALUES = {"lead", "supporting", "extra", "unclassified"}
_LEAD_ROLE_RE = re.compile(
    r"(?:^|[/、，,\s])(?:男主角?|女主角?|主角|主人公|第一主角|核心主角)(?:$|[/、，,\s])"
)
_SUPPORTING_ROLE_RE = re.compile(
    r"配角|反派|对手|常驻角色|阶段角色|阶段核心|核心证人|关键人物|重要角色|"
    r"证人|目击者|伏击者|爪牙|下属|家属"
)
_EXTRA_ROLE_RE = re.compile(
    r"群演|群众|路人|无名角色|人群|围观者|乘客群|村民群|店员群|保安群|守卫群|工作人员群"
)


def _character_name_keys(value: Any) -> set[str]:
    text = str(value or "").strip().casefold()
    if not text:
        return set()
    base = re.sub(r"\s*[（(][^）)]{1,24}[）)]\s*$", "", text).strip()
    return {text, base} if base else {text}


def _story_bible_character_lookup(
    production_context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    bible = dict(production_context.get("confirmed_story_bible") or {})
    content = bible.get("content")
    characters = content.get("characters", []) if isinstance(content, dict) else []
    canonical: dict[str, dict[str, Any]] = {}
    alias_owners: dict[str, list[dict[str, Any]]] = {}
    for character in characters:
        if not isinstance(character, dict):
            continue
        for key in _character_name_keys(character.get("name", "")):
            canonical[key] = character
        for value in character.get("aliases") or []:
            for key in _character_name_keys(value):
                alias_owners.setdefault(key, []).append(character)
    lookup = dict(canonical)
    for key, owners in alias_owners.items():
        unique = {id(owner): owner for owner in owners}
        if key not in canonical and len(unique) == 1:
            lookup[key] = next(iter(unique.values()))
    return lookup


_STORY_CHARACTER_PROFILE_FIELDS = (
    "role",
    "age",
    "appearance",
    "personality",
    "goal",
    "conflict",
    "arc",
    "costume",
    "voice",
    "narrative_function",
    "appearance_scope",
)


def _enrich_character_candidate_from_story_bible(
    requirement_type: str,
    candidate: dict[str, Any],
    production_context: dict[str, Any],
) -> dict[str, Any]:
    """Fill missing extracted character fields from the confirmed Story Bible."""
    if requirement_type != "character":
        return candidate
    result = dict(candidate)
    aliases = list(
        dict.fromkeys(
            str(value).strip()
            for value in [result.get("name", ""), *(result.get("aliases") or [])]
            if str(value).strip()
        )
    )
    lookup = _story_bible_character_lookup(production_context)
    story_character = next(
        (
            lookup.get(key)
            for value in aliases
            for key in _character_name_keys(value)
            if lookup.get(key)
        ),
        None,
    )
    if story_character is None:
        return result

    result["aliases"] = list(
        dict.fromkeys(
            [
                *aliases,
                str(story_character.get("name") or "").strip(),
                *[
                    str(value).strip()
                    for value in story_character.get("aliases") or []
                    if str(value).strip()
                ],
            ]
        )
    )
    attributes = dict(result.get("attributes") or {})
    story_character_id = str(story_character.get("character_id") or "").strip()
    if story_character_id:
        attributes["story_character_id"] = story_character_id
    filled_fields: list[str] = []
    for field in _STORY_CHARACTER_PROFILE_FIELDS:
        value = story_character.get(field)
        if attributes.get(field) in (None, "") and isinstance(value, (str, int)):
            text = str(value).strip()
            if text:
                attributes[field] = text
                filled_fields.append(field)
    description = str(story_character.get("description") or "").strip()
    if not str(result.get("description") or "").strip() and description:
        result["description"] = description
        filled_fields.append("description")
    if filled_fields:
        bible = dict(production_context.get("confirmed_story_bible") or {})
        attributes["story_bible_profile_fields"] = sorted(set(filled_fields))
        attributes["story_bible_profile_version"] = bible.get("version")
        attributes["story_bible_character_name"] = story_character.get("name")
    result["attributes"] = attributes
    return result


def _classify_character_role(
    aliases: list[str],
    attributes: dict[str, Any],
    production_context: dict[str, Any],
) -> tuple[str, str]:
    """Map narrative character tiers to the asset-library role contract."""

    lookup = _story_bible_character_lookup(production_context)
    story_character = next(
        (
            lookup.get(str(value).strip().casefold())
            for value in aliases
            if str(value).strip() and lookup.get(str(value).strip().casefold()) is not None
        ),
        None,
    )
    if story_character is not None:
        importance = str(story_character.get("importance") or "").strip().lower()
        story_role = str(story_character.get("role") or "")
        if importance == "core":
            return "lead", "confirmed_story_bible.importance=core"
        if importance in {"recurring", "phase"}:
            return "supporting", f"confirmed_story_bible.importance={importance}"
        if importance == "functional":
            if _EXTRA_ROLE_RE.search(story_role):
                return "extra", "confirmed_story_bible.functional_extra"
            return "supporting", "confirmed_story_bible.importance=functional"

    explicit = str(attributes.get("character_role") or "").strip().lower()
    if explicit in _CHARACTER_ROLE_VALUES - {"unclassified"}:
        return explicit, str(
            attributes.get("character_role_source") or "asset_breakdown.character_role"
        )
    role_text = " ".join(
        str(value)
        for value in (
            attributes.get("role"),
            attributes.get("narrative_function"),
            attributes.get("appearance_scope"),
        )
        if value
    )
    if _EXTRA_ROLE_RE.search(role_text):
        return "extra", "asset_breakdown.role_text"
    if _LEAD_ROLE_RE.search(role_text):
        return "lead", "asset_breakdown.role_text"
    if _SUPPORTING_ROLE_RE.search(role_text):
        return "supporting", "asset_breakdown.role_text"
    return "unclassified", "insufficient_role_evidence"


def _apply_character_role_classification(
    requirement_type: str,
    aliases: list[str],
    attributes: dict[str, Any],
    production_context: dict[str, Any],
) -> dict[str, Any]:
    if requirement_type != "character":
        return attributes
    character_role, source = _classify_character_role(aliases, attributes, production_context)
    attributes["character_role"] = character_role
    attributes["character_role_source"] = source
    return attributes


def _normalize_asset_classification(
    requirement_type: str,
    asset_type: str,
    entry: dict[str, Any],
) -> tuple[str, str, list[str], dict[str, Any]]:
    """Keep physical remains out of the reusable character-asset category.

    Models occasionally treat a named person's remains as a character. That
    would create a character asset and later invite image generation for a
    non-character evidence object. Reclassify only explicit remains markers;
    leave ordinary deceased characters and other ambiguous cases untouched.
    """
    aliases = [
        str(value).strip()
        for value in [entry.get("name", ""), *(entry.get("aliases") or [])]
        if str(value).strip()
    ]
    attributes = dict(entry.get("attributes") or {})
    name = str(entry.get("name") or "").strip()
    if requirement_type != "character" or not _REMAINS_MARKER_RE.search(name):
        return requirement_type, asset_type, aliases, attributes

    identity = re.sub(r"[（(][^）)]*[）)]", "", name)
    identity = _REMAINS_MARKER_RE.sub("", identity).strip(" -·:：")
    if identity and identity not in aliases:
        aliases.append(identity)
    attributes.update(
        {
            "source_requirement_type": "character",
            "classification_reason": "明确的遗骨/遗骸/遗体候选按实体证物处理",
            "needs_review": True,
            "story_function": attributes.get("story_function") or "人物遗骸证物",
        }
    )
    return "prop", "prop", list(dict.fromkeys(aliases)), attributes


def _deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(
        {
            json.dumps(record, ensure_ascii=False, sort_keys=True): record for record in records
        }.values()
    )


def _merge_asset_candidate(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    aliases = list(
        dict.fromkeys(
            [
                *(target.get("aliases") or []),
                source.get("name", ""),
                *(source.get("aliases") or []),
            ]
        )
    )
    target["aliases"] = [value for value in aliases if str(value).strip()][:20]
    target["normalized_aliases"] = sorted(_candidate_aliases(target))
    target["episode_numbers"] = sorted(
        set((target.get("episode_numbers") or []) + (source.get("episode_numbers") or []))
    )
    target["attributes"] = {
        **dict(source.get("attributes") or {}),
        **dict(target.get("attributes") or {}),
    }
    target["usage_records"] = _deduplicate_records(
        [
            *(target.get("usage_records") or []),
            *(source.get("usage_records") or []),
        ]
    )
    target["source_records"] = _deduplicate_records(
        [
            *(target.get("source_records") or []),
            *(source.get("source_records") or []),
        ]
    )
    left_scope, right_scope = dict(target.get("narrative_scope") or {}), dict(source.get("narrative_scope") or {})
    if left_scope or right_scope:
        target["narrative_scope"] = {
            **right_scope, **left_scope,
            "episodes": _deduplicate_records([*(left_scope.get("episodes") or []), *(right_scope.get("episodes") or [])]),
            "unit_ids": sorted(set(left_scope.get("unit_ids") or []) | set(right_scope.get("unit_ids") or [])),
        }
    target["source_script_revisions"] = {
        **dict(source.get("source_script_revisions") or {}),
        **dict(target.get("source_script_revisions") or {}),
    }
    if len(str(source.get("description") or "")) > len(str(target.get("description") or "")):
        target["description"] = source.get("description")
    if not target.get("prompt_anchor"):
        target["prompt_anchor"] = source.get("prompt_anchor") or ""
    for key in ("matched_asset_id", "formal_asset_id"):
        if target.get(key) is None and source.get(key) is not None:
            target[key] = source[key]
    target["needs_review"] = _candidate_requires_review(target)
    if target["needs_review"]:
        target["readiness_status"] = "source_review"
    elif target.get("readiness_status") == "source_review":
        target["readiness_status"] = (
            "matched" if target.get("matched_asset_id") else "material_missing"
        )
    return target


def _preserve_candidate_outside_scope(
    candidate: dict[str, Any],
    selected_types: set[str],
    selected_numbers: set[int],
) -> dict[str, Any] | None:
    if not candidate.get("selected", True) or candidate.get("merged_into_candidate_id"):
        return None
    preserved = dict(candidate)
    requirement_type = str(preserved.get("requirement_type") or preserved.get("asset_type") or "")
    if requirement_type not in selected_types:
        return preserved
    if not selected_numbers:
        return None
    episode_numbers = {int(value) for value in preserved.get("episode_numbers") or []}
    if not episode_numbers:
        return preserved
    remaining_numbers = episode_numbers - selected_numbers
    if not remaining_numbers:
        return None
    preserved["episode_numbers"] = sorted(remaining_numbers)
    if preserved.get("narrative_scope"):
        from app.services.creation_asset_scope import candidate_narrative_scope
        preserved["narrative_scope"] = candidate_narrative_scope(
            preserved["episode_numbers"], preserved["narrative_scope"]
        )
    preserved["usage_records"] = [
        record
        for record in preserved.get("usage_records") or []
        if int(record.get("episode_number") or 0) in remaining_numbers
    ]
    preserved["source_records"] = [
        record
        for record in preserved.get("source_records") or []
        if record.get("episode_number") is None
        or int(record.get("episode_number") or 0) in remaining_numbers
    ]
    preserved["source_assignment_status"] = (
        "episode" if preserved["source_records"] else "unassigned"
    )
    return preserved


def _merge_candidate_list(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.get("selected", True) or candidate.get("merged_into_candidate_id"):
            candidate["normalized_aliases"] = sorted(_candidate_aliases(candidate))
            merged.append(candidate)
            continue
        matched_id = candidate.get("matched_asset_id") or candidate.get("formal_asset_id")
        existing = next(
            (
                entry
                for entry in merged
                if entry.get("selected", True)
                and not entry.get("merged_into_candidate_id")
                and _candidates_match(entry, candidate)
                and (
                    matched_id is None
                    or (entry.get("matched_asset_id") or entry.get("formal_asset_id"))
                    in {None, matched_id}
                )
            ),
            None,
        )
        if existing is None and matched_id is not None:
            existing = next(
                (
                    entry
                    for entry in merged
                    if entry.get("selected", True)
                    and not entry.get("merged_into_candidate_id")
                    and entry.get("asset_type") == candidate.get("asset_type")
                    and (entry.get("requirement_type") or entry.get("asset_type"))
                    == (candidate.get("requirement_type") or candidate.get("asset_type"))
                    and matched_id
                    == (entry.get("matched_asset_id") or entry.get("formal_asset_id"))
                ),
                None,
            )
        if existing is None:
            candidate["normalized_aliases"] = sorted(_candidate_aliases(candidate))
            candidate["needs_review"] = _candidate_requires_review(candidate)
            merged.append(candidate)
        else:
            _merge_asset_candidate(existing, candidate)
    return merged


from app.services.creation_session_service import (
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
)


async def finalize_creation_job(session: AsyncSession, job: Job, result: dict[str, Any]) -> None:
    if job.target_type not in CREATION_ARTIFACT_TYPES or job.target_id is None:
        return
    from app.services import script_continuity_review_service

    if job.target_type == script_continuity_review_service.TARGET_SCRIPT_CONTINUITY_CHECK:
        await script_continuity_review_service.finalize_check(session, job, result)
        return
    if job.target_type == script_continuity_review_service.TARGET_SCRIPT_CONTINUITY_REPAIR:
        await script_continuity_review_service.finalize_repair(session, job, result)
        return

    if job.target_type in {
        JOB_TARGET_EPISODE_SCRIPT_GENERATION,
        JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
        JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    }:
        from app.services.creation_finalize_handlers import finalize_episode_job

        await finalize_episode_job(session, job, result)
        return

    item = await session.get(CreationSession, job.target_id)
    if item is None or item.owner_id != job.owner_id:
        raise NotFoundError("创作会话不存在")
    if job.target_type in {
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    }:
        from app.services.creation_finalize_assets import finalize_asset_breakdown

        await finalize_asset_breakdown(session, item, job, result)
        return

    from app.services.creation_finalize_handlers import finalize_session_job

    await finalize_session_job(session, item, job, result)


async def mark_creation_job_failed(session: AsyncSession, job: Job) -> None:
    from app.services.creation_finalize_failures import mark_creation_job_failed as recover

    await recover(session, job)
