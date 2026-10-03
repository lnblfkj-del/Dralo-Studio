"""Deterministic character-ecosystem guidance and soft quality review."""

from typing import Any
from uuid import uuid4

from app.core.errors import ValidationError


CHARACTER_TIERS = ("core", "recurring", "phase", "functional")


def character_target_range(episode_count: int) -> tuple[int, int]:
    if episode_count <= 10:
        return 3, 5
    if episode_count <= 30:
        return 4, 7
    if episode_count <= 60:
        return 5, 9
    # Longer stories need room for rotating chapter casts, not a fixed season cap.
    chapters = (min(episode_count, 300) - 60 + 29) // 30
    return 5 + chapters, 10 + chapters * 2


def character_ecosystem_prompt(episode_count: int) -> str:
    minimum, maximum = character_target_range(episode_count)
    return f"""角色生态要求：计划 {episode_count} 集，建议规划 {minimum}-{maximum} 位有姓名、会重复出现的角色；这是软目标，不是硬凑人数。
每个 characters 项除 name、role、goal、conflict、arc 外，还必须包含 importance、narrative_function、appearance_scope。
importance 只能是 core、recurring、phase、functional：core 承担主线与核心冲突，recurring 是持续推动关系或支线的常驻角色，phase 只服务特定阶段/篇章，functional 是临时功能角色。
先保证至少一位 core，并为长篇提供能轮换矛盾、关系和信息功能的 recurring/phase 角色。不得把同一叙事职责拆成多个换名角色，也不得为了达到数量虚构无作用人物。
按题材、用户要求和故事阶段分配主线、关系、对立及信息职责；这些职责可以由同一人物承担，不强制每种职责新增一人。
长篇区分全程核心/常驻人物与轮换的阶段人物，建议范围不是人数上限，不要求所有角色每集登场。
若原文或故事确实只需要少量角色，允许低于建议范围，在 character_ecosystem.small_cast_reason 写明具体叙事依据，不得为了达标编造原文人物。
appearance_scope 用自然语言说明出场安排，同时 appearance_ranges 必须给出可校验的 start/end 集数范围；全程角色使用第1集到最终集。"""


def review_character_ecosystem(content: dict[str, Any], episode_count: int) -> dict[str, Any]:
    characters = list(content.get("characters") or [])
    previous_review = content.get("character_ecosystem") or {}
    small_cast_reason = str(previous_review.get("small_cast_reason") or "").strip()
    minimum, maximum = character_target_range(episode_count)
    named_recurring = [
        row for row in characters
        if str(row.get("name") or "").strip()
        and row.get("importance") != "functional"
    ]
    tiers = {str(row.get("importance") or "") for row in characters}
    warnings: list[dict[str, str]] = []
    if len(named_recurring) < minimum and not small_cast_reason:
        warnings.append({
            "code": "character_count_below_recommended",
            "message": f"当前仅 {len(named_recurring)} 位持续/阶段角色，{episode_count} 集项目建议 {minimum}-{maximum} 位；可说明叙事依据后保留当前人数，不必机械凑数。",
        })
    if "core" not in tiers:
        warnings.append({"code": "missing_core_character", "message": "尚未标记核心角色，主线责任可能不清晰。"})
    if episode_count > 10 and "recurring" not in tiers:
        warnings.append({"code": "missing_recurring_support", "message": "长篇项目缺少常驻配角，关系和冲突可能难以持续轮换。"})
    if any(not str(row.get("narrative_function") or "").strip() for row in characters):
        warnings.append({"code": "missing_narrative_function", "message": "部分角色尚未填写叙事职责，无法判断是否重复或必要。"})
    if content.get("phase_plan"):
        for index, person in enumerate(characters):
            if not person.get("importance") or not person.get("appearance_ranges"):
                warnings.append({"code": f"character_scope_{index}", "message": f"角色{person['name']}缺少角色层级或明确出场集数范围。"})
        for index, phase in enumerate(content["phase_plan"]):
            span = phase["episode_range"]
            if not any(
                r["start"] <= span["end"] and r["end"] >= span["start"]
                for person in named_recurring
                for r in person.get("appearance_ranges", [])
            ):
                warnings.append({"code": f"phase_cast_{index}", "message": f"第{span['start']}-{span['end']}集阶段没有安排持续或阶段角色。"})
    return {
        "episode_count": episode_count,
        "recommended_min": minimum,
        "recommended_max": maximum,
        "named_story_character_count": len(named_recurring),
        "small_cast_reason": small_cast_reason,
        "warnings": warnings,
    }


def enrich_story_bible(content: dict[str, Any], episode_count: int, *, assign_ids: bool = True) -> dict[str, Any]:
    enriched = dict(content)
    if not assign_ids:
        enriched["character_ecosystem"] = review_character_ecosystem(enriched, episode_count)
        return enriched
    enriched["characters"] = [
        {**person, "character_id": person.get("character_id") or f"char_{uuid4().hex}"}
        for person in content.get("characters", [])
    ]
    enriched["event_timeline"] = [
        {**event, "event_id": event.get("event_id") or f"event_{uuid4().hex}"}
        for event in content.get("event_timeline", [])
    ]
    for person in enriched["characters"]:
        for span in person.get("appearance_ranges", []):
            if span["end"] > episode_count:
                raise ValidationError("角色出场范围超过项目计划集数")
    for event in enriched["event_timeline"]:
        span = event.get("episode_range")
        if (span and span["end"] > episode_count) or (event.get("episode_hint") or 0) > episode_count:
            raise ValidationError("事件集数范围超过项目计划集数")
    enriched["character_ecosystem"] = review_character_ecosystem(enriched, episode_count)
    return enriched
