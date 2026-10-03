"""Derive story-specific creative directions from the brief and editable understanding."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def understanding_fingerprint(
    *,
    brief: str,
    genre: str = "",
    conflict: str = "",
    characters: str = "",
    tone: str = "",
) -> str:
    """候选方案的输入指纹：创意 + 四项理解。

    先归一化空白，避免纯排版改动就让候选集体过期。
    """

    def normalize(value: str) -> str:
        return " ".join((value or "").split())

    payload = json.dumps(
        {
            "brief": normalize(brief),
            "genre": normalize(genre),
            "conflict": normalize(conflict),
            "characters": normalize(characters),
            "tone": normalize(tone),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _clip(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _phrase(value: str) -> str:
    text = " ".join((value or "").split())
    for separator in ("。", "；", ";", "，", ",", "、", "/", " "):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return _clip(text, 16)


def propose_creative_directions(
    *,
    brief: str,
    genre: str = "",
    conflict: str = "",
    characters: str = "",
    tone: str = "",
) -> list[dict[str, str]]:
    seed = _phrase(genre) or _phrase(brief) or "原创意"
    conflict_text = conflict.strip() or f"{seed}里尚未揭开的核心矛盾"
    people = characters.strip() or "主要人物之间的立场变化"
    mood = tone.strip() or "与题材匹配的叙事基调"
    mystery_title = _phrase(conflict) or f"{seed}·证据与谜题"
    relation_title = _phrase(characters) or f"{seed}·关系推进"
    stakes_title = _phrase(tone) or f"{seed}·动机博弈"
    return [
        {
            "id": "mystery",
            "title": mystery_title,
            "spine": f"主线围绕「{conflict_text}」展开，把关键信息拆进每一集的发现、误导和集尾钩子。",
            "relationships": f"人物关系服从查清问题：{people}之间的信息差、隐瞒与对质推动剧情。",
            "difference": "和其他方案相比，本方案优先把事件链条做实，人物关系为揭开真相服务。",
        },
        {
            "id": "relationship",
            "title": relation_title,
            "spine": f"主线围绕{people}的关系变化展开，外部事件用来逼出信任、背叛或同盟。",
            "relationships": f"每一集都要让关系前进一步或裂开一道口子，而不是只交代案情。",
            "difference": f"和其他方案相比，本方案把「{relation_title}」写成持续推进的人物戏，谜题只是压力来源。",
        },
        {
            "id": "stakes",
            "title": stakes_title,
            "spine": f"主线围绕人物选择与对手博弈展开，基调保持「{mood}」，让每集都有输赢交换。",
            "relationships": f"{people}因立场不同不断改写同盟，关系是博弈的筹码而不是背景板。",
            "difference": "和其他方案相比，本方案强调动机、代价和反击节奏，而不是先铺完整证据链。",
        },
    ]


def serialize_proposals(proposals: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "spine": item["spine"],
            "relationships": item["relationships"],
            "difference": item["difference"],
        }
        for item in proposals
    ]
