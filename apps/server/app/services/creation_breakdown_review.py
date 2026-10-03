"""M3 剧本研读与资产拆解业务逻辑：分批研读、资产候选生成与确认入库。

本模块处理长剧本的分批 AI 研读和结构化资产拆解的完整流程。
"""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    ASSET_TYPE_CHARACTER,
    ASSET_TYPE_COSTUME,
    ASSET_TYPE_PROP,
    ASSET_TYPE_SCENE,
    ASSET_TYPE_VOICE,
    Asset,
    CreationSession,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
)
from app.services import (
    asset_service,
    project_service,
    script_finalization_service,
)
from app.services.script_source_snapshot import script_snapshot_is_current
from app.services.creation_breakdown_sources import _validate_breakdown_story_source


R4_REQUIREMENT_GROUPS = (
    ("character", ASSET_TYPE_CHARACTER, "characters"),
    ("costume", ASSET_TYPE_COSTUME, "costumes"),
    ("scene", ASSET_TYPE_SCENE, "scenes"),
    ("prop", ASSET_TYPE_PROP, "props"),
    ("character_voice", ASSET_TYPE_VOICE, "character_voices"),
    ("music", ASSET_TYPE_VOICE, "music"),
    ("ambience", ASSET_TYPE_VOICE, "ambience"),
    ("sound_effect", ASSET_TYPE_VOICE, "sound_effects"),
)
R4_REQUIREMENT_TYPES = tuple(entry[0] for entry in R4_REQUIREMENT_GROUPS)
BREAKDOWN_ACTIVE_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
}


async def _asset_breakdown_for_review(
    session: AsyncSession, item: CreationSession
) -> tuple[dict[str, Any], dict[str, Any]]:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    settings = dict(item.settings)
    breakdown = dict(settings.get("asset_breakdown") or {})
    if breakdown.get("status") != "awaiting_confirmation":
        raise ConflictError("没有待审阅的资产拆解方案")
    source_revisions = dict(breakdown.get("source_script_revisions") or {})
    fingerprint = str(breakdown.get("input_fingerprint") or "")
    current = (
        await script_snapshot_is_current(session, item.project_id, fingerprint)
        if fingerprint
        else (
            await script_finalization_service.revisions_are_current(
                session, item.project_id, source_revisions
            )
        )
    )
    if not current:
        breakdown.update(
            {
                "status": "stale",
                "completed": False,
                "stale_reason": "正式剧本版本已变化，请重新执行资产拆解",
            }
        )
        settings["asset_breakdown"] = breakdown
        item.settings = settings
        flag_modified(item, "settings")
        await session.flush()
        raise ConflictError("正式剧本版本已变化，请重新执行资产拆解")
    await _validate_breakdown_story_source(session, item, dict(breakdown.get("story_source") or {}))
    # Normalize deterministic model classification issues before any review or
    # confirmation action. This also upgrades pending results created before
    # the classifier was added, without another provider call.
    from app.services.creation_finalize_service import (
        _apply_character_role_classification,
        _candidate_aliases,
        _candidate_requires_review,
        _candidates_match,
        _enrich_character_candidate_from_story_bible,
        _merge_candidate_list,
        _merge_asset_candidate,
        _normalize_asset_classification,
    )

    pending_candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    existing_assets = await asset_service.list_assets(session, item.project_id)
    existing_by_id = {int(asset["id"]): asset for asset in existing_assets}
    normalized_candidates: list[dict[str, Any]] = []
    for candidate in pending_candidates:
        from app.services.creation_breakdown_validation import preserve_matched_asset_scope

        matched = existing_by_id.get(int(candidate.get("matched_asset_id") or 0))
        if matched is not None and not candidate.get("episode_scope_reviewed"):
            preserve_matched_asset_scope(candidate, dict(matched.get("attributes") or {}))
        candidate = _enrich_character_candidate_from_story_bible(
            str(candidate.get("requirement_type") or candidate.get("asset_type") or ""),
            candidate,
            dict(breakdown.get("production_context") or {}),
        )
        previous_requirement_type = str(
            candidate.get("requirement_type") or candidate.get("asset_type") or ""
        )
        previous_asset_type = str(candidate.get("asset_type") or "")
        previous_needs_review = bool(candidate.get("needs_review"))
        previous_attributes = dict(candidate.get("attributes") or {})
        requirement_type, asset_type, aliases, attributes = _normalize_asset_classification(
            previous_requirement_type,
            previous_asset_type,
            candidate,
        )
        aliases = list(dict.fromkeys(str(value).strip() for value in aliases if str(value).strip()))
        candidate["requirement_type"] = requirement_type
        candidate["asset_type"] = asset_type
        candidate["aliases"] = aliases
        candidate["normalized_aliases"] = sorted(_candidate_aliases(candidate))
        candidate["attributes"] = _apply_character_role_classification(
            requirement_type,
            aliases,
            attributes,
            dict(breakdown.get("production_context") or {}),
        )
        attributes = candidate["attributes"]
        candidate["needs_review"] = _candidate_requires_review(candidate)
        if (
            requirement_type != previous_requirement_type
            or asset_type != previous_asset_type
            or candidate["needs_review"] != previous_needs_review
            or attributes != previous_attributes
        ):
            candidate["_classification_changed"] = True
        normalized_candidates.append(candidate)

    normalized_candidates = _merge_candidate_list(normalized_candidates)
    changed = (
        normalized_candidates != pending_candidates
        or any(candidate.get("_classification_changed") for candidate in normalized_candidates)
    )
    if changed:
        for candidate in normalized_candidates:
            if not candidate.pop("_classification_changed", False):
                continue
            aliases = set(candidate.get("normalized_aliases") or [])
            target = next(
                (
                    existing
                    for existing in normalized_candidates
                    if existing is not candidate
                    and not existing.get("merged_into_candidate_id")
                    and existing.get("asset_type") == candidate.get("asset_type")
                    and existing.get("requirement_type") == candidate.get("requirement_type")
                    and existing.get("selected", True)
                    and _candidates_match(existing, candidate)
                ),
                None,
            )
            if target is not None:
                _merge_asset_candidate(target, candidate)
                candidate["selected"] = False
                candidate["merged_into_candidate_id"] = target.get("candidate_id")
        for candidate in normalized_candidates:
            candidate.pop("_classification_changed", None)
        breakdown["candidates"] = normalized_candidates
    _refresh_asset_candidate_counts(breakdown, existing_assets=existing_assets)
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return settings, breakdown


def _refresh_asset_candidate_counts(
    breakdown: dict[str, Any], *, existing_assets: list[dict[str, Any]] | None = None
) -> None:
    from app.services.creation_breakdown_validation import dialogue_coverage_issues, reconcile_dialogue_coverage
    from app.services.creation_finalize_assets import _character_alias_issues, _character_coverage_issues

    candidates = list(breakdown.get("candidates") or [])
    reconcile_dialogue_coverage(candidates, dict(breakdown.get("production_context") or {}))
    refresh_codes = {"asset_episode_scope_reduced", "dialogue_character_missing", "missing_core_character",
                     "core_character_without_episodes", "core_character_episode_gap"}
    if existing_assets is not None:
        refresh_codes.add("character_alias_conflict")
    issues = [issue for issue in breakdown.get("validation_issues") or []
              if issue.get("code") not in refresh_codes]
    if existing_assets is not None:
        issues.extend(_character_alias_issues(candidates, existing_assets))
    if "character" in (breakdown.get("requirement_types") or []) or any(row.get("requirement_type") == "character" for row in candidates):
        issues.extend(dialogue_coverage_issues(candidates, dict(breakdown.get("production_context") or {})))
    issues.extend(_character_coverage_issues(
        candidates, dict(breakdown.get("production_context") or {}),
        full_character_scope=("character" in (breakdown.get("requirement_types") or [])
                              and breakdown.get("scope_mode") != "partial"),
    ))
    breakdown["validation_issues"] = issues
    breakdown["blocking_issue_count"] = sum(issue.get("severity") == "blocking" for issue in issues)
    breakdown["candidate_counts"] = {
        requirement_type: sum(
            candidate.get("requirement_type", candidate.get("asset_type")) == requirement_type
            and candidate.get("selected", True)
            and not candidate.get("merged_into_candidate_id")
            for candidate in candidates
        )
        for requirement_type in R4_REQUIREMENT_TYPES
    }
    breakdown["selected_count"] = sum(
        candidate.get("selected", True) and not candidate.get("merged_into_candidate_id")
        for candidate in candidates
    )


_CHARACTER_PROFILE_TEXT_FIELDS = (
    "age",
    "appearance",
    "personality",
    "goal",
    "conflict",
    "arc",
    "costume",
    "voice",
)
_CLASSIFIED_CHARACTER_ROLES = {"lead", "supporting", "extra"}
_AUDIO_PROMPT_SUFFIXES = {
    "character_voice": "中文角色配音，保持音色、口音、音高与语速一致，无背景音乐。",
    "music": "完整配乐素材，无对白、无环境音，保留自然起止与可剪辑结构。",
    "ambience": "自然循环的干净环境底噪，无对白、无配乐。",
    "sound_effect": "单一干净音效，无对白、无配乐，保留清晰起音与自然衰减。",
}


def _refresh_candidate_review_state(candidate: dict[str, Any]) -> None:
    from app.services.creation_finalize_service import _candidate_requires_review

    candidate["needs_review"] = _candidate_requires_review(candidate)
    if candidate["needs_review"]:
        candidate["readiness_status"] = "source_review"
    elif candidate.get("readiness_status") == "source_review":
        candidate["readiness_status"] = (
            "matched" if candidate.get("matched_asset_id") else "material_missing"
        )


async def fill_missing_audio_candidate_prompts(
    session: AsyncSession, item: CreationSession, user_id: int
) -> dict[str, int]:
    settings, breakdown = await _asset_breakdown_for_review(session, item)
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    filled = 0
    now = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        requirement_type = str(
            candidate.get("requirement_type") or candidate.get("asset_type") or ""
        )
        if (
            not candidate.get("selected", True)
            or candidate.get("merged_into_candidate_id")
            or requirement_type not in _AUDIO_PROMPT_SUFFIXES
            or str(candidate.get("prompt_anchor") or "").strip()
        ):
            continue
        description = str(candidate.get("description") or "").strip()
        if not description:
            continue
        candidate["prompt_anchor"] = (
            f"{description.rstrip('。')}。{_AUDIO_PROMPT_SUFFIXES[requirement_type]}"
        )
        attributes = dict(candidate.get("attributes") or {})
        attributes.update(
            {
                "prompt_source": "description_fallback",
                "prompt_filled_by": user_id,
                "prompt_filled_at": now,
            }
        )
        candidate["attributes"] = attributes
        _refresh_candidate_review_state(candidate)
        filled += 1
    breakdown["candidates"] = candidates
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return {"filled_count": filled}


async def acknowledge_formal_script_candidate_reviews(
    session: AsyncSession, item: CreationSession, user_id: int
) -> dict[str, int]:
    settings, breakdown = await _asset_breakdown_for_review(session, item)
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    acknowledged = 0
    now = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        source_records = list(candidate.get("source_records") or [])
        if (
            not candidate.get("selected", True)
            or candidate.get("merged_into_candidate_id")
            or not candidate.get("needs_review")
            or not str(candidate.get("prompt_anchor") or "").strip()
            or not source_records
            or any(record.get("source_kind") != "formal_script" for record in source_records)
        ):
            continue
        attributes = dict(candidate.get("attributes") or {})
        attributes.update(
            {
                "needs_review": False,
                "review_resolution": "accepted_formal_script",
                "reviewed_by": user_id,
                "reviewed_at": now,
            }
        )
        candidate["attributes"] = attributes
        candidate["usage_records"] = [
            {**record, "needs_review": False}
            for record in candidate.get("usage_records") or []
        ]
        _refresh_candidate_review_state(candidate)
        if not candidate["needs_review"]:
            acknowledged += 1
    breakdown["candidates"] = candidates
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return {"acknowledged_count": acknowledged}


async def _sync_character_profile(
    session: AsyncSession,
    project_id: int,
    asset: Asset,
    attributes: dict[str, Any],
    *,
    increment_revision: bool,
) -> None:
    if asset.asset_type != ASSET_TYPE_CHARACTER:
        return
    link = await asset_service.get_project_link(session, project_id, asset.id)
    data = dict(link.production_data or {})
    profile = dict(data.get("profile") or {})
    current_role = str(profile.get("character_role") or "unclassified")
    inferred_role = str(attributes.get("character_role") or "unclassified")
    resolved_role = current_role if current_role in _CLASSIFIED_CHARACTER_ROLES else inferred_role
    if resolved_role not in _CLASSIFIED_CHARACTER_ROLES:
        resolved_role = "unclassified"

    changed = profile.get("character_role") != resolved_role
    profile["character_role"] = resolved_role
    aliases = list(
        dict.fromkeys(
            [
                *(profile.get("aliases") or []),
                *(attributes.get("aliases") or []),
            ]
        )
    )[:30]
    if profile.get("aliases") != aliases:
        profile["aliases"] = aliases
        changed = True
    for field in _CHARACTER_PROFILE_TEXT_FIELDS:
        value = attributes.get(field)
        if profile.get(field) in (None, "") and isinstance(value, (str, int)):
            profile[field] = str(value)
            changed = True
    if asset.description and not profile.get("description"):
        profile["description"] = asset.description
        changed = True

    if current_role in _CLASSIFIED_CHARACTER_ROLES and asset.project_id == project_id:
        asset_attributes = dict(asset.attributes or {})
        asset_attributes["character_role"] = current_role
        asset_attributes["character_role_source"] = "project_profile"
        asset.attributes = asset_attributes
    if not changed:
        return
    data["profile"] = profile
    link.production_data = data
    if increment_revision:
        link.production_revision += 1


async def _backfill_completed_character_roles(
    session: AsyncSession,
    item: CreationSession,
    settings: dict[str, Any],
    breakdown: dict[str, Any],
) -> None:
    from app.services.creation_finalize_service import (
        _apply_character_role_classification,
        _enrich_character_candidate_from_story_bible,
    )

    production_context = dict(breakdown.get("production_context") or {})
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    for index, candidate in enumerate(candidates):
        if (
            candidate.get("asset_type") != ASSET_TYPE_CHARACTER
            or not candidate.get("selected", True)
            or candidate.get("merged_into_candidate_id")
        ):
            continue
        candidate = _enrich_character_candidate_from_story_bible(
            "character", candidate, production_context
        )
        candidates[index] = candidate
        attributes = _apply_character_role_classification(
            "character",
            [candidate.get("name", ""), *(candidate.get("aliases") or [])],
            dict(candidate.get("attributes") or {}),
            production_context,
        )
        candidate["attributes"] = attributes
        asset_id = candidate.get("formal_asset_id") or candidate.get("matched_asset_id")
        asset = await session.get(Asset, int(asset_id)) if asset_id else None
        if asset is None:
            continue
        asset_attributes = dict(asset.attributes or {})
        for key, value in attributes.items():
            if asset_attributes.get(key) in (None, "") and value not in (None, ""):
                asset_attributes[key] = value
        if (
            asset.project_id == item.project_id
            and str(asset_attributes.get("character_role") or "unclassified")
            not in _CLASSIFIED_CHARACTER_ROLES
        ):
            asset_attributes["character_role"] = attributes["character_role"]
            asset_attributes["character_role_source"] = attributes["character_role_source"]
        asset.attributes = asset_attributes
        await _sync_character_profile(
            session,
            item.project_id,
            asset,
            asset_attributes if asset.project_id == item.project_id else attributes,
            increment_revision=True,
        )
    breakdown["candidates"] = candidates
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()


async def update_script_asset_candidate(
    session: AsyncSession,
    item: CreationSession,
    candidate_id: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    settings, breakdown = await _asset_breakdown_for_review(session, item)
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    candidate = next(
        (entry for entry in candidates if int(entry.get("candidate_id", 0)) == candidate_id),
        None,
    )
    if candidate is None:
        raise NotFoundError("资产候选不存在")
    if candidate.get("merged_into_candidate_id"):
        raise ConflictError("已合并的资产候选不能继续编辑")

    if "episode_numbers" in data:
        valid_numbers = {
            episode.number
            for episode in await project_service.list_episodes(session, item.project_id)
        }
        invalid = sorted(set(data["episode_numbers"] or []) - valid_numbers)
        if invalid:
            raise ConflictError(f"资产候选包含不存在的分集: {invalid}")
    if "matched_asset_id" in data and data["matched_asset_id"] is not None:
        matched = await asset_service.get_asset(
            session, item.project_id, int(data["matched_asset_id"])
        )
        if matched.asset_type != candidate.get("asset_type"):
            raise ConflictError("只能匹配相同类型的正式资产")
        if matched.asset_type == ASSET_TYPE_VOICE:
            purpose = str((matched.attributes or {}).get("audio_purpose") or "character_voice")
            if purpose != candidate.get("requirement_type"):
                raise ConflictError("只能匹配相同声音用途的正式资产")

    candidate.update(data)
    if "episode_numbers" in data:
        candidate["episode_scope_reviewed"] = True
        candidate["usage_records"] = [record for record in candidate.get("usage_records") or []
                                      if record.get("episode_number") in data["episode_numbers"]]
        production_context = dict(breakdown.get("production_context") or {})
        episode_sources = {
            int(entry.get("episode_number")): entry
            for entry in production_context.get("episodes", [])
            if entry.get("episode_number") is not None
        }
        candidate["source_records"] = [
            {
                "kind": "episode",
                "episode_number": number,
                "locator": episode_sources[number].get("locator"),
                "script_revision": episode_sources[number].get("script_revision"),
                "source_kind": episode_sources[number].get("source_kind"),
            }
            for number in candidate.get("episode_numbers", [])
            if number in episode_sources
        ]
        candidate["source_assignment_status"] = (
            "episode" if candidate["source_records"] else "unassigned"
        )
    if "name" in data or "aliases" in data:
        candidate["normalized_aliases"] = sorted(
            {
                str(value).strip().casefold()
                for value in [candidate.get("name", ""), *(candidate.get("aliases") or [])]
                if str(value).strip()
            }
        )
    breakdown["candidates"] = candidates
    _refresh_asset_candidate_counts(
        breakdown, existing_assets=await asset_service.list_assets(session, item.project_id)
    )
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return candidate


async def merge_script_asset_candidate(
    session: AsyncSession,
    item: CreationSession,
    candidate_id: int,
    target_candidate_id: int,
) -> dict[str, Any]:
    if candidate_id == target_candidate_id:
        raise ConflictError("资产候选不能合并到自身")
    settings, breakdown = await _asset_breakdown_for_review(session, item)
    candidates = [dict(candidate) for candidate in breakdown.get("candidates") or []]
    source = next(
        (entry for entry in candidates if int(entry.get("candidate_id", 0)) == candidate_id),
        None,
    )
    target = next(
        (entry for entry in candidates if int(entry.get("candidate_id", 0)) == target_candidate_id),
        None,
    )
    if source is None or target is None:
        raise NotFoundError("资产候选不存在")
    if source.get("merged_into_candidate_id") or target.get("merged_into_candidate_id"):
        raise ConflictError("已合并的资产候选不能再次合并")
    if source.get("asset_type") != target.get("asset_type"):
        raise ConflictError("只有相同类型的资产候选可以合并")
    if source.get("requirement_type") != target.get("requirement_type"):
        raise ConflictError("不同制作需求不能合并")

    target["aliases"] = list(
        dict.fromkeys(
            [
                *(target.get("aliases") or []),
                source.get("name", ""),
                *(source.get("aliases") or []),
            ]
        )
    )[:20]
    target["normalized_aliases"] = sorted(
        {
            str(value).strip().casefold()
            for value in [target.get("name", ""), *target["aliases"]]
            if str(value).strip()
        }
    )
    target["episode_numbers"] = sorted(
        set((target.get("episode_numbers") or []) + (source.get("episode_numbers") or []))
    )
    target["attributes"] = {
        **dict(source.get("attributes") or {}),
        **dict(target.get("attributes") or {}),
    }
    target["usage_records"] = [
        *(target.get("usage_records") or []),
        *(source.get("usage_records") or []),
    ]
    merged_source_records: list[dict[str, Any]] = []
    seen_source_records: set[str] = set()
    for record in [*(target.get("source_records") or []), *(source.get("source_records") or [])]:
        marker = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if marker not in seen_source_records:
            seen_source_records.add(marker)
            merged_source_records.append(record)
    target["source_records"] = merged_source_records
    target["needs_review"] = bool(target.get("needs_review") or source.get("needs_review"))
    if len(str(source.get("description") or "")) > len(str(target.get("description") or "")):
        target["description"] = source.get("description")
    if not target.get("prompt_anchor"):
        target["prompt_anchor"] = source.get("prompt_anchor") or ""
    source["selected"] = False
    source["merged_into_candidate_id"] = target_candidate_id
    breakdown["candidates"] = candidates
    _refresh_asset_candidate_counts(
        breakdown, existing_assets=await asset_service.list_assets(session, item.project_id)
    )
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await session.flush()
    return target
