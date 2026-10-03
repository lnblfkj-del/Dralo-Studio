"""Small, page-batched project summaries for the home workbench."""

from sqlalchemy import case, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetVersion, MediaFile, ProjectAssetLink
from app.models.agent_config import StylePreset
from app.models.project import Episode, EpisodeProduction, Project
from app.models.script_version import ScriptVersion
from app.models.user import User


async def project_card_summaries(session: AsyncSession, projects: list[Project]) -> dict[int, dict]:
    if not projects:
        return {}
    ids = [project.id for project in projects]
    episode_rows = (await session.execute(
        select(Episode.project_id, func.count(Episode.id),
               func.sum(case((EpisodeProduction.final_media_file_id.is_not(None), 1), else_=0)),
               func.sum(Episode.duration_estimate), func.count(Episode.duration_estimate))
        .outerjoin(EpisodeProduction, EpisodeProduction.episode_id == Episode.id)
        .where(Episode.project_id.in_(ids)).group_by(Episode.project_id)
    )).all()
    episodes = {row[0]: row for row in episode_rows}

    asset_projects = union(
        select(Asset.project_id.label("project_id"), Asset.id.label("asset_id"))
        .where(Asset.project_id.in_(ids)),
        select(ProjectAssetLink.project_id, ProjectAssetLink.asset_id)
        .where(ProjectAssetLink.project_id.in_(ids), ProjectAssetLink.production_archived.is_(False)),
    ).subquery()
    ranked = select(
        asset_projects.c.project_id, MediaFile.id.label("media_id"),
        func.row_number().over(
            partition_by=asset_projects.c.project_id,
            order_by=(AssetVersion.is_final.desc(), case((Asset.asset_type == "scene", 0), else_=1),
                      AssetVersion.updated_at.desc(), AssetVersion.id.desc()),
        ).label("position"),
    ).join(Asset, Asset.id == asset_projects.c.asset_id).join(
        AssetVersion, AssetVersion.asset_id == Asset.id,
    ).join(MediaFile, MediaFile.id == AssetVersion.media_file_id).where(MediaFile.kind == "image").subquery()
    covers = dict((await session.execute(
        select(ranked.c.project_id, ranked.c.media_id).where(ranked.c.position == 1)
    )).all())

    contributors = union(
        select(Project.id.label("project_id"), Project.owner_id.label("user_id")).where(Project.id.in_(ids)),
        select(Episode.project_id, ScriptVersion.created_by)
        .join(ScriptVersion, ScriptVersion.episode_id == Episode.id)
        .where(Episode.project_id.in_(ids), ScriptVersion.created_by.is_not(None)),
    ).subquery()
    people: dict[int, list[dict]] = {}
    for project_id, user_id, display_name, username in (await session.execute(
        select(contributors.c.project_id, User.id, User.display_name, User.username)
        .join(User, User.id == contributors.c.user_id).order_by(User.id)
    )).all():
        people.setdefault(project_id, []).append({"id": user_id, "name": display_name or username})

    preset_ids = {
        int(style.split(":")[1]) for project in projects
        if (style := str((project.creation_settings or {}).get("style_id", ""))).startswith("preset:")
        and style.split(":")[1].isdigit()
    }
    presets = dict((await session.execute(select(StylePreset.id, StylePreset.name).where(
        StylePreset.id.in_(preset_ids)
    ))).all()) if preset_ids else {}
    legacy_styles = {"retro": "复古胶片", "palace": "古装宫廷", "noir": "黑色电影", "romance": "浪漫唯美",
                     "youth": "青春校园", "everyday": "生活写实", "ink": "水墨国风", "anime": "日系动漫",
                     "storybook": "绘本童话", "clay": "黏土动画", "fantasy": "奇幻", "scifi": "科幻"}
    result = {}
    for project in projects:
        settings = project.creation_settings or {}
        row = episodes.get(project.id)
        actual_count, finished, estimated = (int(row[1]), int(row[2] or 0), int(row[3] or 0)) if row else (0, 0, 0)
        count = max(actual_count, int(settings.get("episode_count") or 0))
        duration = int(settings.get("episode_duration") or 0)
        # Estimates are labelled as such; missing episodes use the project's planned duration.
        known_durations = int(row[4]) if row else 0
        total_duration = estimated + max(0, count - known_durations) * duration
        style = str(settings.get("style_id") or "default")
        style_name = presets.get(int(style.split(":")[1]), "未设置风格") if style.startswith("preset:") and style.split(":")[1].isdigit() else legacy_styles.get(style, "默认风格")
        if style == "custom":
            style_name = "自定义风格"
        result[project.id] = {
            "cover_media_id": covers.get(project.id), "episode_count": count,
            "completed_episodes": finished, "estimated_duration": total_duration,
            "style_name": style_name, "participants": people.get(project.id, []),
        }
    return result
