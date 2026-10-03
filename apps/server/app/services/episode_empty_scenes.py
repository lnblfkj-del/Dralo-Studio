"""Recognize untouched placeholder scenes without replacing authored work."""

import re
from sqlalchemy import select
from app.core.errors import ConflictError
from app.models import AssetUsage, EpisodeProductionPlan, Scene, Shot


async def empty_placeholders(session, episode):
    scenes = list((await session.scalars(select(Scene).where(Scene.episode_id == episode.id))).all())
    if not scenes:
        return []
    ids = [scene.id for scene in scenes]
    has_shots = await session.scalar(select(Shot.id).where(Shot.scene_id.in_(ids)).limit(1))
    has_refs = await session.scalar(select(AssetUsage.id).where(AssetUsage.scene_id.in_(ids)).limit(1))
    has_plan = await session.scalar(select(EpisodeProductionPlan.id).where(EpisodeProductionPlan.episode_id == episode.id).limit(1))
    if has_shots or has_refs or has_plan or any(
        not re.fullmatch(r"场景\s*\d+", scene.name or "") or scene.location or scene.time_of_day
        or (scene.description or "").strip() not in {"", "待完善场景描述"}
        for scene in scenes
    ):
        raise ConflictError("本集已有正式场景或引用，不能用首次拆解覆盖，请保留现有内容后重规划")
    return scenes
