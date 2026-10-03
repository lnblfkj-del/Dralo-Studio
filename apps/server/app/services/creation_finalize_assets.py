"""Asset-breakdown result finalization for creation jobs."""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ModelOutputBusinessValidationError
from app.models import (
    ASSET_TYPE_CHARACTER,
    ASSET_TYPE_COSTUME,
    ASSET_TYPE_PROP,
    ASSET_TYPE_SCENE,
    ASSET_TYPE_VOICE,
    CreationSession,
    Job,
)
from app.schemas.creation import ScriptAssetBreakdownResult
from app.services import asset_service, script_finalization_service
from app.services.creation_asset_scope import candidate_narrative_scope
from app.services.creation_breakdown_planning import REQUIREMENT_RESULT_KEYS
from app.services.creation_breakdown_validation import (
    dialogue_coverage_issues,
    preserve_matched_asset_scope,
    reconcile_dialogue_coverage,
)
from app.services.creation_finalize_service import (
    _apply_character_role_classification,
    _candidate_requires_review,
    _candidates_match,
    _enrich_character_candidate_from_story_bible,
    _merge_candidate_list,
    _normalize_asset_classification,
    _preserve_candidate_outside_scope,
)
from app.services.creation_session_service import (
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    append_message,
    parse_structured_result,
)
from app.services.script_source_snapshot import script_snapshot_is_current

ALL_REQUIREMENT_TYPES = {
    "character",
    "costume",
    "scene",
    "prop",
    "character_voice",
    "music",
    "ambience",
    "sound_effect",
}


def _identity_values(attributes: dict[str, Any], requirement_type: str) -> set[str]:
    keys = ("story_character_id",) if requirement_type == "character" else (
        ("linked_asset_key", "asset_key") if requirement_type == "character_voice" else ("asset_key",)
    )
    values = {
        f"{key}:{str(attributes[key]).strip().casefold()}"
        for key in keys if str(attributes.get(key) or "").strip()
    }
    return {value for value in values if value}


def _match_existing_asset(
    existing_assets: list[dict[str, Any]],
    *,
    asset_type: str,
    requirement_type: str,
    name: str,
    aliases: list[str],
    attributes: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Resolve an asset without allowing a character alias to steal identity."""

    eligible = []
    for existing in existing_assets:
        if existing["asset_type"] != asset_type:
            continue
        existing_attributes = dict(existing.get("attributes") or {})
        existing_requirement = (
            str(existing_attributes.get("audio_purpose") or "character_voice")
            if existing["asset_type"] == ASSET_TYPE_VOICE
            else str(existing_attributes.get("requirement_type") or existing["asset_type"])
        )
        if existing_requirement == requirement_type:
            eligible.append(existing)

    identity = _identity_values(attributes, requirement_type)
    if identity:
        matches = [
            entry
            for entry in eligible
            if identity.intersection(_identity_values(dict(entry.get("attributes") or {}), requirement_type))
        ]
        if len(matches) == 1:
            return int(matches[0]["id"]), None
        if len(matches) > 1:
            return None, "稳定身份同时命中多个正式资产，请人工选择"

    canonical_name = str(name or "").strip().casefold()
    exact = [
        entry
        for entry in eligible
        if str(entry.get("name") or "").strip().casefold() == canonical_name
    ]
    if len(exact) == 1:
        return int(exact[0]["id"]), None
    if len(exact) > 1:
        return None, "正式资产存在同类型同名记录，请先处理重复资产"

    normalized_aliases = {
        str(value).strip().casefold()
        for value in [name, *aliases]
        if str(value).strip()
    }
    alias_matches = []
    for entry in eligible:
        existing_attributes = dict(entry.get("attributes") or {})
        existing_aliases = {
            str(value).strip().casefold()
            for value in existing_attributes.get("aliases") or []
            if str(value).strip()
        }
        if normalized_aliases.intersection(existing_aliases):
            alias_matches.append(entry)
    if requirement_type == "character" and alias_matches:
        return None, "角色名称仅命中已有资产别名，为避免角色串档请人工选择正式资产"
    if len(alias_matches) == 1:
        return int(alias_matches[0]["id"]), None
    if len(alias_matches) > 1:
        return None, "名称或别名命中多个正式资产，请人工选择"
    return None, None


def _character_coverage_issues(
    candidates: list[dict[str, Any]],
    production_context: dict[str, Any],
    *,
    full_character_scope: bool,
) -> list[dict[str, Any]]:
    if not full_character_scope:
        return []
    bible = dict(production_context.get("confirmed_story_bible") or {})
    content = bible.get("content") if isinstance(bible.get("content"), dict) else {}
    characters = content.get("characters", []) if isinstance(content, dict) else []
    character_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("requirement_type") == "character"
        and candidate.get("selected", True)
        and not candidate.get("merged_into_candidate_id")
    ]
    all_episode_numbers = {
        int(row.get("episode_number") or 0)
        for row in production_context.get("episodes") or []
        if int(row.get("episode_number") or 0) > 0
    }
    issues: list[dict[str, Any]] = []
    for character in characters:
        role = str(character.get("role") or "") if isinstance(character, dict) else ""
        if not isinstance(character, dict) or (
            character.get("importance") != "core" and "主角" not in role
        ):
            continue
        character_id = str(character.get("character_id") or "").strip().casefold()
        name = str(character.get("name") or "").strip()
        matched = next(
            (
                candidate
                for candidate in character_candidates
                if (
                    character_id
                    and str(
                        dict(candidate.get("attributes") or {}).get("story_character_id")
                        or ""
                    ).strip().casefold()
                    == character_id
                )
                or str(candidate.get("name") or "").strip().casefold()
                == name.casefold()
            ),
            None,
        )
        if matched is None:
            issues.append({
                "code": "missing_core_character",
                "severity": "blocking",
                "character_name": name,
                "message": f"核心角色“{name}”未出现在资产候选中，已阻止覆盖正式资产。",
            })
            continue
        episodes = {int(value) for value in matched.get("episode_numbers") or []}
        if not episodes:
            issues.append({
                "code": "core_character_without_episodes",
                "severity": "blocking",
                "character_name": name,
                "candidate_id": matched.get("candidate_id"),
                "message": f"核心角色“{name}”没有归集到任何剧集，请核对后再入库。",
            })
        appearance_scope = str(character.get("appearance_scope") or "")
        missing = sorted(all_episode_numbers - episodes)
        if missing and any(marker in appearance_scope for marker in ("全季", "全集", "全剧")):
            issues.append({
                "code": "core_character_episode_gap",
                "severity": "warning",
                "character_name": name,
                "candidate_id": matched.get("candidate_id"),
                "episode_numbers": missing,
                "message": f"核心角色“{name}”设定为全剧出场，但缺少第 {'、'.join(map(str, missing))} 集归集。",
            })
    return issues


def _character_alias_issues(
    candidates: list[dict[str, Any]], existing_assets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    character_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("requirement_type") == "character"
        and candidate.get("selected", True)
        and not candidate.get("merged_into_candidate_id")
    ]
    canonical_owners: dict[str, set[int | str]] = {}
    canonical_labels: dict[str, str] = {}
    for candidate in character_candidates:
        key = str(candidate.get("name") or "").strip().casefold()
        if key:
            canonical_owners.setdefault(key, set()).add(
                f"candidate:{candidate.get('candidate_id')}"
            )
            canonical_labels[key] = str(candidate.get("name") or "").strip()
    for asset in existing_assets:
        if asset.get("asset_type") != ASSET_TYPE_CHARACTER:
            continue
        key = str(asset.get("name") or "").strip().casefold()
        if key:
            canonical_owners.setdefault(key, set()).add(int(asset["id"]))
            canonical_labels[key] = str(asset.get("name") or "").strip()

    issues: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for candidate in character_candidates:
        candidate_id = int(candidate.get("candidate_id") or 0)
        own_name = str(candidate.get("name") or "").strip().casefold()
        own_owners: set[int | str] = {f"candidate:{candidate_id}"}
        if candidate.get("matched_asset_id") is not None:
            own_owners.add(int(candidate["matched_asset_id"]))
        for alias in candidate.get("aliases") or []:
            alias_key = str(alias).strip().casefold()
            marker = (candidate_id, alias_key)
            foreign_owners = canonical_owners.get(alias_key, set()) - own_owners
            if alias_key and alias_key != own_name and foreign_owners and marker not in seen:
                seen.add(marker)
                issues.append({
                    "code": "character_alias_conflict",
                    "severity": "blocking",
                    "character_name": candidate.get("name"),
                    "candidate_id": candidate_id,
                    "message": (
                        f"角色“{candidate.get('name')}”的别名“{alias}”与角色“"
                        f"{canonical_labels.get(alias_key, alias)}”的正式名称冲突。"
                    ),
                })
    return issues


def _requested_episode_numbers(
    progress: dict[str, Any], parameters: dict[str, Any]
) -> set[int]:
    """Keep an explicit empty full-project scope from falling back to one child range."""
    if "requested_episode_numbers" in progress:
        values = progress.get("requested_episode_numbers") or []
    elif "requested_episode_numbers" in parameters:
        values = parameters.get("requested_episode_numbers") or []
    else:
        values = parameters.get("episode_numbers") or []
    return {int(value) for value in values}


async def finalize_asset_breakdown(
    session: AsyncSession, item: CreationSession, job: Job, result: dict[str, Any]
) -> None:
    from app.services.creation_breakdown_service import (
        validate_script_asset_breakdown_job_sources,
    )

    await validate_script_asset_breakdown_job_sources(session, job, require_parent_active=True)
    breakdown = parse_structured_result(
        str(result.get("text", "")),
        ScriptAssetBreakdownResult,
        "剧本资产拆解结果",
    )
    source_revisions: dict[str, Any] = {}
    production_context: dict[str, Any] = {}
    result_groups = (
        "characters",
        "costumes",
        "scenes",
        "props",
        "character_voices",
        "music",
        "ambience",
        "sound_effects",
    )
    if job.target_type == JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH:
        parameters = dict(job.payload.get("parameters") or {})
        production_context = dict(parameters.get("production_context") or {})
        batch_index = int(parameters.get("batch_index", 0))
        total_batches = int(parameters.get("total_batches", 1))
        expected_numbers = {int(value) for value in parameters.get("episode_numbers", [])}
        selected_types = set(parameters.get("requirement_types") or [])
        unexpected_groups = [
            result_key
            for requirement_type, result_key in REQUIREMENT_RESULT_KEYS.items()
            if requirement_type not in selected_types and breakdown[result_key]
        ]
        if unexpected_groups:
            raise ModelOutputBusinessValidationError(
                "资产拆解返回了当前类别任务未请求的资产组",
                details={"unexpected_result_groups": unexpected_groups},
            )
        returned_numbers = {
            int(number)
            for group in result_groups
            for entry in breakdown[group]
            for number in [
                *entry.get("episode_numbers", []),
                *[
                    usage.get("episode_number")
                    for usage in entry.get("usage_records", [])
                ],
            ]
        }
        if not returned_numbers.issubset(expected_numbers):
            raise ModelOutputBusinessValidationError(
                f"第 {batch_index + 1} 批资产返回了输入范围外的集号",
                details={
                    "actual_episode_numbers": sorted(returned_numbers),
                    "expected_episode_numbers": sorted(expected_numbers),
                },
            )
        settings = dict(item.settings)
        progress = dict(settings.get("asset_breakdown") or {})
        production_context = dict(production_context or progress.get("production_context") or {})
        source_revisions = dict(progress.get("source_script_revisions") or {})
        batches = dict(progress.get("batches") or {})
        scope_key = str(parameters.get("scope_key") or "")
        result_slot = scope_key or str(batch_index)
        batches[result_slot] = breakdown
        scope_order = list(progress.get("scope_order") or [])
        visual_scope_keys = list(progress.get("visual_scope_keys") or [])
        progress.update(
            {
                "status": "running",
                "total_batches": total_batches,
                "completed_batches": len(batches),
                "batches": batches,
                "parent_job_id": job.parent_job_id,
            }
        )
        settings["asset_breakdown"] = progress
        item.settings = settings
        visual_batches = int(progress.get("visual_batches") or 0)
        visual_complete = (
            all(key in batches for key in visual_scope_keys)
            if visual_scope_keys
            else all(str(index) in batches for index in range(visual_batches))
        )
        if (
            parameters.get("requirement_group") == "visual"
            and visual_batches
            and visual_complete
            and not progress.get("audio_queued")
        ):
            parent = await session.get(Job, job.parent_job_id) if job.parent_job_id else None
            if parent is None:
                raise ConflictError("资产拆解父任务不存在")
            from app.services.creation_breakdown_jobs import queue_pending_audio_breakdown_jobs

            await queue_pending_audio_breakdown_jobs(
                session,
                item,
                parent,
                (
                    [batches[key] for key in visual_scope_keys]
                    if visual_scope_keys
                    else [batches[str(index)] for index in range(visual_batches)]
                ),
            )
            progress.update({"audio_queued": True, "stage": "audio"})
            settings["asset_breakdown"] = progress
            item.settings = settings
        completed_slots = (
            [key for key in scope_order if key in batches]
            if scope_order
            else list(batches)
        )
        if len(completed_slots) < total_batches:
            await session.flush()
            return
        fingerprint = str(progress.get("input_fingerprint") or "")
        current = (
            await script_snapshot_is_current(session, item.project_id or 0, fingerprint)
            if fingerprint
            else await script_finalization_service.revisions_are_current(
                session, item.project_id or 0, source_revisions
            )
        )
        if not current:
            progress.update(
                {
                    "status": "stale",
                    "completed": False,
                    "stale_reason": "正式剧本版本已变化，本次资产拆解结果未写入待确认区",
                }
            )
            settings["asset_breakdown"] = progress
            item.settings = settings
            await append_message(
                session,
                item.id,
                "assistant",
                "agent_error",
                "资产拆解期间正式剧本发生变化；结果已停止应用，请基于最新确认版本重新拆解。",
                job_id=job.id,
            )
            await session.flush()
            return
        breakdown = {
            "reply": "全部批次资产拆解完成",
            **{
                group: [
                    entry
                    for slot in (scope_order or [str(index) for index in range(total_batches)])
                    for entry in batches[slot].get(group, [])
                ]
                for group in result_groups
            },
        }
    else:
        production_context = dict(
            (job.payload.get("parameters") or {}).get("production_context") or {}
        )
        source_revisions = dict(production_context.get("source_script_revisions") or {})
    pending_progress = dict(item.settings.get("asset_breakdown") or {})
    job_parameters = dict(job.payload.get("parameters") or {})
    selected_types = set(
        pending_progress.get("requirement_types")
        or job_parameters.get("requirement_types")
        or []
    )
    selected_numbers = _requested_episode_numbers(pending_progress, job_parameters)
    partial_scope = bool(
        pending_progress.get("scope_mode") == "partial"
        or selected_numbers
        or (selected_types and selected_types != ALL_REQUIREMENT_TYPES)
    )
    if not partial_scope and not any(breakdown[group] for group in result_groups):
        raise ModelOutputBusinessValidationError("模型未返回任何剧本资产，请重新生成")
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    existing_assets = await asset_service.list_assets(session, item.project_id)

    candidates: list[dict[str, Any]] = []
    groups = (
        ("character", ASSET_TYPE_CHARACTER, breakdown["characters"]),
        ("costume", ASSET_TYPE_COSTUME, breakdown["costumes"]),
        ("scene", ASSET_TYPE_SCENE, breakdown["scenes"]),
        ("prop", ASSET_TYPE_PROP, breakdown["props"]),
        ("character_voice", ASSET_TYPE_VOICE, breakdown["character_voices"]),
        ("music", ASSET_TYPE_VOICE, breakdown["music"]),
        ("ambience", ASSET_TYPE_VOICE, breakdown["ambience"]),
        ("sound_effect", ASSET_TYPE_VOICE, breakdown["sound_effects"]),
    )
    context_episode_map = {
        int(entry.get("episode_number")): entry
        for entry in production_context.get("episodes", [])
        if entry.get("episode_number") is not None
    }
    context_source_records = list(production_context.get("source_records") or [])
    narrative_scope = dict(production_context.get("narrative_scope") or {})
    for requirement_type, asset_type, entries in groups:
        for entry in entries:
            requirement_type, asset_type, aliases, entry_attributes = (
                _normalize_asset_classification(
                    requirement_type,
                    asset_type,
                    entry,
                )
            )
            enriched = _enrich_character_candidate_from_story_bible(
                requirement_type,
                {
                    "name": entry["name"],
                    "aliases": aliases,
                    "description": entry["description"],
                    "attributes": entry_attributes,
                },
                production_context,
            )
            aliases = list(enriched.get("aliases") or aliases)
            entry_description = str(enriched.get("description") or "")
            entry_attributes = dict(enriched.get("attributes") or {})
            entry_attributes = _apply_character_role_classification(
                requirement_type,
                aliases,
                entry_attributes,
                production_context,
            )
            normalized = {value.casefold() for value in aliases}
            entry_numbers = sorted(set(entry.get("episode_numbers", [])))
            entry_scope = candidate_narrative_scope(entry_numbers, narrative_scope)
            candidate_source_records = []
            for number in entry_numbers:
                episode_source = context_episode_map.get(int(number))
                if episode_source is not None:
                    candidate_source_records.append(
                        {
                            "kind": "episode",
                            "episode_number": int(number),
                            "locator": episode_source.get("locator"),
                            "script_revision": episode_source.get("script_revision"),
                            "source_kind": episode_source.get("source_kind"),
                        }
                    )
            if not candidate_source_records:
                candidate_source_records = context_source_records
            usage_records = []
            for usage in entry.get("usage_records", []):
                usage = dict(usage)
                episode_source = context_episode_map.get(int(usage["episode_number"]))
                usage_records.append(
                    {
                        **usage,
                        "locator": episode_source.get("locator") if episode_source else None,
                        "script_revision": episode_source.get("script_revision")
                        if episode_source
                        else None,
                    }
                )
            entry_attributes["requirement_type"] = requirement_type
            if asset_type == ASSET_TYPE_VOICE:
                entry_attributes["audio_purpose"] = requirement_type
            matched = next(
                (
                    candidate
                    for candidate in candidates
                    if _candidates_match(
                        candidate,
                        {
                            "asset_type": asset_type,
                            "requirement_type": requirement_type,
                            "name": entry["name"],
                            "aliases": aliases,
                            "attributes": entry_attributes,
                        },
                    )
                ),
                None,
            )
            if matched is not None:
                matched["aliases"] = list(dict.fromkeys([*matched["aliases"], *aliases]))
                matched["normalized_aliases"] = sorted(
                    set(matched["normalized_aliases"]).union(normalized)
                )
                matched["episode_numbers"] = sorted(
                    set(matched["episode_numbers"] + entry.get("episode_numbers", []))
                )
                matched["narrative_scope"] = candidate_narrative_scope(
                    matched["episode_numbers"], narrative_scope
                )
                if len(entry_description) > len(matched["description"]):
                    matched["description"] = entry_description
                if not matched.get("prompt_anchor") and entry.get("prompt_anchor"):
                    matched["prompt_anchor"] = entry["prompt_anchor"].strip()
                matched["attributes"] = {**matched["attributes"], **entry_attributes}
                matched["usage_records"] = [
                    *matched.get("usage_records", []),
                    *usage_records,
                ]
                existing_records = {
                    json.dumps(record, ensure_ascii=False, sort_keys=True)
                    for record in matched.get("source_records", [])
                }
                for record in candidate_source_records:
                    marker = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    if marker not in existing_records:
                        matched.setdefault("source_records", []).append(record)
                        existing_records.add(marker)
                matched["needs_review"] = _candidate_requires_review(matched)
                continue
            matched_asset_id, identity_conflict = _match_existing_asset(
                existing_assets,
                asset_type=asset_type,
                requirement_type=requirement_type,
                name=str(entry.get("name") or ""),
                aliases=aliases,
                attributes=entry_attributes,
            )
            if identity_conflict:
                entry_attributes["identity_match_status"] = "needs_review"
                entry_attributes["identity_match_reason"] = identity_conflict
            candidates.append(
                {
                    "candidate_id": len(candidates) + 1,
                    "asset_type": asset_type,
                    "requirement_type": requirement_type,
                    "name": entry["name"].strip(),
                    "aliases": aliases,
                    "normalized_aliases": sorted(normalized),
                    "episode_numbers": entry_numbers,
                    "narrative_scope": entry_scope,
                    "description": entry_description.strip(),
                    "prompt_anchor": entry["prompt_anchor"].strip(),
                    "attributes": entry_attributes,
                    "usage_records": usage_records,
                    "selected": True,
                    "matched_asset_id": matched_asset_id,
                    "source_kind": production_context.get("source_kind"),
                    "source_name": production_context.get("source_name"),
                    "source_text_sha256": production_context.get("source_text_sha256"),
                    "source_records": candidate_source_records,
                    "source_script_revisions": dict(
                        production_context.get("source_script_revisions") or {}
                    ),
                    "input_fingerprint": production_context.get("input_fingerprint"),
                    "confirmed_story_bible_version": (
                        (production_context.get("confirmed_story_bible") or {}).get("version")
                    ),
                    "source_assignment_status": "episode" if entry_numbers else "unassigned",
                    "needs_review": bool(
                        entry_attributes.get("needs_review")
                        or identity_conflict
                        or any(record.get("needs_review") for record in usage_records)
                        or not entry["prompt_anchor"].strip()
                    ),
                }
            )
    progress = pending_progress
    selected_numbers = _requested_episode_numbers(progress, job_parameters)
    if progress.get("scope_mode") == "partial":
        preserved = [
            scoped
            for candidate in progress.get("preserved_candidates", [])
            if (
                scoped := _preserve_candidate_outside_scope(
                    candidate, selected_types, selected_numbers
                )
            )
            is not None
        ]
        candidates = _merge_candidate_list([*candidates, *preserved])
    for candidate_id, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = candidate_id
        candidate["needs_review"] = _candidate_requires_review(candidate)
        requirement_type = str(candidate.get("requirement_type") or candidate.get("asset_type"))
        if candidate.get("matched_asset_id") is None:
            matched_asset_id, identity_conflict = _match_existing_asset(
                existing_assets,
                asset_type=str(candidate.get("asset_type") or ""),
                requirement_type=requirement_type,
                name=str(candidate.get("name") or ""),
                aliases=list(candidate.get("aliases") or []),
                attributes=dict(candidate.get("attributes") or {}),
            )
            candidate["matched_asset_id"] = matched_asset_id
            if identity_conflict:
                attributes = dict(candidate.get("attributes") or {})
                attributes["identity_match_status"] = "needs_review"
                attributes["identity_match_reason"] = identity_conflict
                candidate["attributes"] = attributes
                candidate["needs_review"] = True
        matched = next(
            (
                asset
                for asset in existing_assets
                if asset["id"] == candidate.get("matched_asset_id")
            ),
            None,
        )
        if matched:
            preserve_matched_asset_scope(candidate, dict(matched.get("attributes") or {}))
        candidate["readiness_status"] = (
            "source_review"
            if candidate.get("needs_review")
            else "ready"
            if matched and any(version.get("is_final") for version in matched.get("versions", []))
            else "matched"
            if matched
            else "material_missing"
        )
    counts = {
        requirement_type: sum(
            candidate.get("requirement_type") == requirement_type for candidate in candidates
        )
        for requirement_type, _, _ in groups
    }
    reconcile_dialogue_coverage(candidates, production_context)
    validation_issues = [
        *(dialogue_coverage_issues(candidates, production_context)
          if "character" in selected_types or any(row.get("requirement_type") == "character" for row in candidates) else []),
        *_character_alias_issues(candidates, existing_assets),
        *_character_coverage_issues(
            candidates,
            production_context,
            full_character_scope=(
                "character" in selected_types
                and not selected_numbers
                and progress.get("scope_mode") != "partial"
            ),
        ),
    ]
    issue_candidate_ids = {
        int(issue["candidate_id"])
        for issue in validation_issues
        if issue.get("candidate_id") is not None
    }
    for candidate in candidates:
        if int(candidate.get("candidate_id") or 0) not in issue_candidate_ids:
            continue
        candidate["needs_review"] = True
        candidate["readiness_status"] = "source_review"
    settings = dict(item.settings)
    settings["asset_breakdown"] = {
        "status": "awaiting_confirmation",
        "completed": False,
        "job_id": job.id,
        "candidate_counts": counts,
        "candidates": candidates,
        "source_script_revisions": source_revisions,
        "input_fingerprint": str(
            (job.payload.get("parameters") or {}).get("input_fingerprint")
            or (item.settings.get("asset_breakdown") or {}).get("input_fingerprint")
            or ""
        ),
        "story_source": dict(
            job_parameters.get("story_source")
            or progress.get("story_source")
            or production_context.get("story_source")
            or {}
        ),
        "production_context": production_context,
        "requirement_types": sorted(selected_types),
        "requested_episode_numbers": sorted(selected_numbers),
        "scope_mode": progress.get("scope_mode")
        or ("partial" if selected_numbers or len(selected_types) < 8 else "full"),
        "validation_issues": validation_issues,
        "blocking_issue_count": sum(
            issue.get("severity") == "blocking" for issue in validation_issues
        ),
    }
    item.settings = settings
    await append_message(
        session,
        item.id,
        "assistant",
        "asset_breakdown",
        f"资产方案已生成，共 {len(candidates)} 项；确认后才会写入资产库。",
        job_id=job.id,
        parameters={"candidate_counts": counts},
    )
    await session.flush()
    return
