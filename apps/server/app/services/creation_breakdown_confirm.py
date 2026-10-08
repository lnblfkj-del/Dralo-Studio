"""M3 剧本研读与资产拆解业务逻辑：分批研读、资产候选生成与确认入库。

本模块处理长剧本的分批 AI 研读和结构化资产拆解的完整流程。
"""

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import ConflictError
from app.models import (
    ASSET_TYPE_CHARACTER,
    ASSET_TYPE_COSTUME,
    ASSET_TYPE_PROP,
    ASSET_TYPE_SCENE,
    ASSET_TYPE_VOICE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    Asset,
    CreationSession,
)
from app.services import (
    asset_service,
    project_service,
    script_finalization_service,
)
from app.services.creation_asset_scope import (
    build_narrative_asset_scope,
    refresh_script_scope,
)
from app.services.creation_breakdown_review import (
    _asset_breakdown_for_review,
    _backfill_completed_character_roles,
    _sync_character_profile,
)
from app.services.creation_breakdown_sources import _validate_breakdown_story_source
from app.services.creation_session_service import append_message
from app.services.script_source_snapshot import script_snapshot_is_current

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


async def confirm_script_asset_breakdown(
    session: AsyncSession, item: CreationSession
) -> dict[str, Any]:
    if item.project_id is None:
        raise ConflictError("当前创作会话尚未关联项目")
    settings = dict(item.settings)
    breakdown = dict(settings.get("asset_breakdown") or {})
    if breakdown.get("status") == "completed":
        await _backfill_completed_character_roles(session, item, settings, breakdown)
        return dict(breakdown.get("created_counts") or {})
    if breakdown.get("status") != "awaiting_confirmation":
        raise ConflictError("没有待确认的资产拆解方案")

    settings, breakdown = await _asset_breakdown_for_review(session, item)

    source_revisions = dict(breakdown.get("source_script_revisions") or {})
    input_fingerprint = str(breakdown.get("input_fingerprint") or "")
    production_context = dict(breakdown.get("production_context") or {})
    narrative_scope = dict(production_context.get("narrative_scope") or {})
    if not narrative_scope:
        episodes = await project_service.list_episodes(session, item.project_id)
        narrative_scope = build_narrative_asset_scope(
            item.settings,
            [
                {
                    "episode_id": episode.id,
                    "episode_number": episode.number,
                    "script_revision": episode.script_revision,
                }
                for episode in episodes
            ],
        )
    current = (
        await script_snapshot_is_current(session, item.project_id, input_fingerprint)
        if input_fingerprint
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
        await session.flush()
        raise ConflictError("正式剧本版本已变化，请重新执行资产拆解")
    await _validate_breakdown_story_source(session, item, dict(breakdown.get("story_source") or {}))

    project = await project_service.get_project(session, item.project_id, item.owner_id)
    existing_assets = await asset_service.list_assets(session, item.project_id)
    used_slugs = {entry["slug"] for entry in existing_assets}
    existing_by_id = {entry["id"]: entry for entry in existing_assets}
    existing_ids = set(existing_by_id)
    selected_candidates = [
        candidate
        for candidate in breakdown.get("candidates", [])
        if candidate.get("selected", True) and not candidate.get("merged_into_candidate_id")
    ]
    if not selected_candidates and breakdown.get("scope_mode") != "partial":
        raise ConflictError("请至少保留一个资产候选")
    blocking_issues = [
        issue
        for issue in breakdown.get("validation_issues") or []
        if issue.get("severity") == "blocking"
    ]
    if blocking_issues:
        raise ConflictError(
            f"资产覆盖检查未通过：{blocking_issues[0].get('message') or '请先处理阻断问题'}"
        )
    seen_names: dict[tuple[str, str, str], int] = {}
    seen_matches: set[int] = set()
    character_names = {
        str(candidate.get("name") or "").strip().casefold(): str(
            candidate.get("name") or ""
        ).strip()
        for candidate in selected_candidates
        if str(candidate.get("requirement_type") or candidate.get("asset_type"))
        == "character"
        and str(candidate.get("name") or "").strip()
    }
    existing_character_names = {
        str(asset.get("name") or "").strip().casefold(): int(asset["id"])
        for asset in existing_assets
        if asset.get("asset_type") == ASSET_TYPE_CHARACTER
        and str(asset.get("name") or "").strip()
    }
    for candidate in selected_candidates:
        key = (
            str(candidate.get("asset_type") or ""),
            str(candidate.get("requirement_type") or candidate.get("asset_type") or ""),
            str(candidate.get("name") or "").strip().casefold(),
        )
        if not key[2]:
            raise ConflictError("资产候选名称不能为空")
        if key in seen_names:
            raise ConflictError("存在同类型同名候选，请先合并或改名")
        seen_names[key] = int(candidate.get("candidate_id") or 0)
        matched_id = candidate.get("matched_asset_id")
        if key[1] == "character":
            conflicting_aliases = sorted(
                {
                    str(alias).strip()
                    for alias in candidate.get("aliases") or []
                    if str(alias).strip().casefold() != key[2]
                    and (
                        str(alias).strip().casefold() in character_names
                        or (
                            str(alias).strip().casefold() in existing_character_names
                            and existing_character_names[str(alias).strip().casefold()]
                            != matched_id
                        )
                    )
                }
            )
            if conflicting_aliases:
                raise ConflictError(
                    f"角色“{candidate.get('name')}”的别名与其他角色正式名称冲突："
                    + "、".join(conflicting_aliases)
                )
        if matched_id is not None:
            if matched_id in seen_matches:
                raise ConflictError("多个候选不能重复匹配同一个正式资产")
            seen_matches.add(matched_id)
    created_counts = {requirement_type: 0 for requirement_type in R4_REQUIREMENT_TYPES}
    formal_asset_ids: list[int] = []
    matched_count = 0
    character_asset_ids: dict[str, int] = {}
    # The pending candidate set is the complete post-merge state. A partial
    # recovery can contain newly generated episodes plus preserved historical
    # episodes, so the original request range is not the confirmation range.
    refreshed_episode_numbers = {
        int(number)
        for candidate in selected_candidates
        for number in candidate.get("episode_numbers") or []
    }
    refreshed_episode_numbers.update(
        int(number) for number in breakdown.get("requested_episode_numbers") or []
    )
    if not refreshed_episode_numbers:
        refreshed_episode_numbers = {
            episode.number
            for episode in await project_service.list_episodes(session, item.project_id)
        }
    refreshed_asset_ids: set[int] = set()
    for existing in existing_assets:
        if existing["asset_type"] != ASSET_TYPE_CHARACTER:
            continue
        for value in [
            existing.get("name", ""),
            *((existing.get("attributes") or {}).get("aliases") or []),
        ]:
            if str(value).strip():
                character_asset_ids[str(value).strip().casefold()] = int(existing["id"])

    def attach_character_relation(attributes: dict[str, Any], requirement_type: str) -> None:
        if requirement_type not in {"costume", "character_voice"}:
            return
        character_names = [
            value.strip()
            for value in re.split(r"[,，、/]+", str(attributes.get("character_name") or ""))
            if value.strip()
        ]
        linked_ids = list(
            dict.fromkeys(
                character_asset_ids[name.casefold()]
                for name in character_names
                if name.casefold() in character_asset_ids
            )
        )
        attributes["linked_character_asset_ids"] = linked_ids
        attributes["linked_character_asset_id"] = (
            linked_ids[0] if len(character_names) == 1 and linked_ids else None
        )
        fully_linked = bool(character_names) and len(linked_ids) == len(character_names)
        attributes["character_relation_status"] = (
            "linked" if fully_linked else "needs_review"
        )
        if not fully_linked:
            attributes["needs_review"] = True

    async def sync_project_script_scope(
        asset: Asset,
        candidate: dict[str, Any] | None,
        requirement_type: str,
    ) -> dict[str, Any]:
        link = await asset_service.get_project_link(session, project.id, asset.id)
        production_data = dict(link.production_data or {})
        current_scope = dict(production_data.get("script_scope") or {})
        script_scope = refresh_script_scope(
            current_scope,
            candidate,
            refreshed_episode_numbers=refreshed_episode_numbers,
            narrative_scope=narrative_scope,
        )
        script_scope.update(
            {
                "source_kind": "script_asset_breakdown",
                "requirement_type": requirement_type,
                "input_fingerprint": input_fingerprint,
            }
        )
        from app.services.asset_audio_profile import effective_profile
        production_data["script_scope"] = script_scope
        if asset.asset_type == "voice":
            production_data["profile"] = effective_profile(asset, production_data)
        if link.production_data != production_data:
            link.production_data = production_data
            link.production_revision += 1
        return script_scope

    for candidate in breakdown.get("candidates", []):
        if not candidate.get("selected", True) or candidate.get("merged_into_candidate_id"):
            continue
        matched_asset_id = candidate.get("matched_asset_id")
        requirement_type = str(candidate.get("requirement_type") or candidate.get("asset_type"))
        if matched_asset_id in existing_ids:
            matched = await asset_service.get_asset(session, project.id, matched_asset_id)
            candidate["matched_asset_id"] = matched.id
            candidate["formal_asset_id"] = matched.id
            formal_asset_ids.append(matched.id)
            refreshed_asset_ids.add(matched.id)
            matched_count += 1
            script_scope = await sync_project_script_scope(
                matched, candidate, requirement_type
            )
            if matched.project_id is not None:
                attributes = dict(matched.attributes or {})
                attributes["aliases"] = list(
                    dict.fromkeys(
                        [
                            *(attributes.get("aliases") or []),
                            *(candidate.get("aliases") or []),
                        ]
                    )
                )
                attributes.update(script_scope)
                attributes["source_kind"] = "script_asset_breakdown"
                attributes["input_fingerprint"] = input_fingerprint
                attributes["source_context_schema"] = production_context.get("schema_version")
                attributes["source_kind_detail"] = production_context.get("source_kind")
                attributes["source_name"] = candidate.get("source_name") or production_context.get(
                    "source_name"
                )
                attributes["source_text_sha256"] = candidate.get(
                    "source_text_sha256"
                ) or production_context.get("source_text_sha256")
                attributes["requirement_type"] = requirement_type
                if matched.asset_type == ASSET_TYPE_VOICE:
                    attributes["audio_purpose"] = requirement_type
                attach_character_relation(attributes, requirement_type)
                candidate_attributes = dict(candidate.get("attributes") or {})
                for key, value in candidate_attributes.items():
                    if (
                        key == "character_role"
                        and str(attributes.get(key) or "unclassified") == "unclassified"
                    ):
                        attributes[key] = value
                    elif key not in attributes:
                        attributes[key] = value
                if candidate_attributes.get("source_conflicts"):
                    attributes["source_conflicts"] = candidate_attributes["source_conflicts"]
                attributes["readiness_status"] = candidate.get("readiness_status") or "matched"
                attributes["confirmed_story_bible_version"] = candidate.get(
                    "confirmed_story_bible_version"
                ) or (production_context.get("confirmed_story_bible") or {}).get("version")
                attributes["needs_review"] = bool(
                    candidate.get("needs_review") or attributes.get("needs_review")
                )
                matched.attributes = attributes
                await _sync_character_profile(
                    session,
                    project.id,
                    matched,
                    attributes,
                    increment_revision=True,
                )
                if matched.asset_type == ASSET_TYPE_CHARACTER:
                    for value in [matched.name, *(attributes.get("aliases") or [])]:
                        if str(value).strip():
                            character_asset_ids[str(value).strip().casefold()] = matched.id
                if not matched.description and candidate.get("description"):
                    matched.description = candidate["description"]
                if not matched.prompt_anchor:
                    matched.prompt_anchor = candidate.get("prompt_anchor") or candidate.get("description") or ""
            continue
        base_slug = (
            re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(candidate.get("name") or "")).strip("-")
            or f"asset-{candidate.get('candidate_id', 0)}"
        )
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        attributes = dict(candidate.get("attributes") or {})
        attributes["aliases"] = list(candidate.get("aliases") or [])
        attributes.update(
            refresh_script_scope(
                {},
                candidate,
                refreshed_episode_numbers=refreshed_episode_numbers,
                narrative_scope=narrative_scope,
            )
        )
        attributes["source_kind"] = "script_asset_breakdown"
        attributes["input_fingerprint"] = input_fingerprint
        attributes["source_context_schema"] = production_context.get("schema_version")
        attributes["source_kind_detail"] = production_context.get("source_kind")
        attributes["source_name"] = candidate.get("source_name") or production_context.get(
            "source_name"
        )
        attributes["source_text_sha256"] = candidate.get(
            "source_text_sha256"
        ) or production_context.get("source_text_sha256")
        attributes["requirement_type"] = requirement_type
        if candidate["asset_type"] == ASSET_TYPE_VOICE:
            attributes["audio_purpose"] = requirement_type
        attach_character_relation(attributes, requirement_type)
        attributes["readiness_status"] = candidate.get("readiness_status") or "material_missing"
        attributes["confirmed_story_bible_version"] = candidate.get(
            "confirmed_story_bible_version"
        ) or (production_context.get("confirmed_story_bible") or {}).get("version")
        attributes["needs_review"] = bool(
            candidate.get("needs_review") or attributes.get("needs_review")
        )
        created = await asset_service.create_asset(
            session,
            project,
            {
                "asset_type": candidate["asset_type"],
                "name": candidate["name"],
                "slug": slug,
                "description": candidate.get("description") or "",
                "prompt_anchor": candidate.get("prompt_anchor") or candidate.get("description") or "",
                "attributes": attributes,
            },
        )
        candidate["matched_asset_id"] = created.id
        candidate["formal_asset_id"] = created.id
        formal_asset_ids.append(created.id)
        refreshed_asset_ids.add(created.id)
        await sync_project_script_scope(created, candidate, requirement_type)
        await _sync_character_profile(
            session,
            project.id,
            created,
            attributes,
            increment_revision=False,
        )
        if created.asset_type == ASSET_TYPE_CHARACTER:
            for value in [created.name, *(attributes.get("aliases") or [])]:
                if str(value).strip():
                    character_asset_ids[str(value).strip().casefold()] = created.id
        created_counts[requirement_type] += 1

    # Absence from an AI extraction is not evidence that a formal asset stopped
    # appearing.  Keep unmatched scopes intact; explicit candidate edits can
    # still remove episodes from a matched asset during confirmation.

    formal_assets = [
        asset
        for asset_id in formal_asset_ids
        if (asset := await session.get(Asset, asset_id)) is not None
    ]
    scene_asset_ids: dict[str, int] = {}
    for asset in formal_assets:
        if asset.asset_type == ASSET_TYPE_CHARACTER:
            for value in [asset.name, *((asset.attributes or {}).get("aliases") or [])]:
                if str(value).strip():
                    character_asset_ids[str(value).strip().casefold()] = asset.id
        if asset.asset_type == ASSET_TYPE_SCENE:
            for value in [asset.name, *((asset.attributes or {}).get("aliases") or [])]:
                if str(value).strip():
                    scene_asset_ids[str(value).strip().casefold()] = asset.id
    for asset in formal_assets:
        attributes = dict(asset.attributes or {})
        requirement_type = str(attributes.get("requirement_type") or asset.asset_type)
        attach_character_relation(attributes, requirement_type)
        if asset.asset_type == ASSET_TYPE_SCENE and attributes.get("base_scene_name"):
            base_name = str(attributes["base_scene_name"]).strip().casefold()
            base_id = scene_asset_ids.get(base_name)
            attributes["linked_base_scene_asset_id"] = base_id
            attributes["scene_relation_status"] = "linked" if base_id else "needs_review"
            if not base_id:
                attributes["needs_review"] = True
        asset.attributes = attributes

    breakdown.update(
        {
            "status": "completed",
            "completed": True,
            "created_counts": created_counts,
            "matched_count": matched_count,
            "formal_asset_ids": formal_asset_ids,
        }
    )
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    flag_modified(item, "settings")
    await append_message(
        session,
        item.id,
        "assistant",
        "asset_breakdown_confirmed",
        "资产方案已确认并写入资产库。",
        parameters={"created_counts": created_counts},
    )
    await session.flush()
    return created_counts


async def reject_script_asset_breakdown(
    session: AsyncSession, item: CreationSession
) -> dict[str, Any]:
    settings = dict(item.settings)
    breakdown = dict(settings.get("asset_breakdown") or {})
    if breakdown.get("status") == "rejected":
        return breakdown
    if breakdown.get("status") != "awaiting_confirmation":
        raise ConflictError("没有待放弃的资产拆解方案")
    breakdown.update({"status": "rejected", "completed": False})
    settings["asset_breakdown"] = breakdown
    item.settings = settings
    await append_message(
        session,
        item.id,
        "assistant",
        "asset_breakdown_rejected",
        "已放弃本次资产拆解方案，资产库未发生变化。",
        job_id=breakdown.get("job_id"),
    )
    await session.flush()
    return breakdown
