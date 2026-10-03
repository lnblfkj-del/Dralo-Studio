"""Batch-only model input, separate from immutable server-side source snapshots."""

from copy import deepcopy
from typing import Any


def batch_prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    episodes = deepcopy(context.get("episodes") or [])
    numbers = {int(row["number"]) for row in episodes}
    fragments = {int(row["number"]): row["script"] for row in episodes if row.get("source_range")}
    source = context.get("production_context") or {}
    episode_sources = [row for row in source.get("episodes") or []
                       if int(row.get("episode_number") or 0) in numbers]
    episode_ids = {row.get("episode_id") for row in episode_sources}
    production = {
        key: deepcopy(source[key])
        for key in ("schema_version", "source_kind", "source_name", "unresolved_policy", "story_source")
        if key in source
    }
    production.update({
        "episode_count": len(episodes),
        "episodes": deepcopy(episode_sources),
        "character_appearances": deepcopy([
            row for row in source.get("character_appearances") or []
            if int(row.get("episode_number") or 0) in numbers
            and (int(row.get("episode_number") or 0) not in fragments
                 or (row.get("source_excerpt") and row["source_excerpt"] in fragments[int(row["episode_number"])]))
        ]),
        "source_records": deepcopy([
            row for row in source.get("source_records") or []
            if row.get("episode_id") in episode_ids
        ]),
    })
    narrative = source.get("narrative_scope") or {}
    production["narrative_scope"] = {
        **{key: deepcopy(narrative[key]) for key in (
            "schema_version", "narrative_structure", "character_reuse"
        ) if key in narrative},
        "episodes": deepcopy([row for row in narrative.get("episodes") or []
                              if int(row.get("episode_number") or 0) in numbers]),
    }
    bible = context.get("story_bible") or (source.get("confirmed_story_bible") or {}).get("content") or {}
    # Global identities are references, not evidence of an appearance in this batch.
    character_keys = (
        "character_id", "name", "aliases", "role", "age", "gender", "description",
        "personality", "appearance", "costume", "voice", "importance", "narrative_function",
    )
    identity_reference = {
        **{key: deepcopy(bible[key]) for key in (
            "title", "genre", "tone", "world", "themes"
        ) if key in bible},
        "characters": [{key: deepcopy(row[key]) for key in character_keys if key in row}
                       for row in bible.get("characters") or [] if isinstance(row, dict)],
    }
    result = {
        "allowed_episode_numbers": sorted(numbers),
        "episodes": episodes,
        "production_context": production,
        "identity_reference": identity_reference,
        "existing_assets": deepcopy(context.get("existing_assets") or []),
        "candidate_reference": deepcopy(context.get("candidate_reference") or []),
        "candidate_reference_policy": "已有候选仅作命名参考。同一资产沿用名称；不同服装状态、场景状态和声音用途不可合并。画外音、传音的说话者也须归入角色及角色声音。仅以本批正文作为出场依据。",
    }
    if "visual_asset_keys" in context:
        result["visual_asset_keys"] = [
            {**deepcopy(row), "episode_numbers": sorted(set(row.get("episode_numbers") or []) & numbers)}
            for row in context["visual_asset_keys"]
            if set(row.get("episode_numbers") or []) & numbers
        ]
    return result
