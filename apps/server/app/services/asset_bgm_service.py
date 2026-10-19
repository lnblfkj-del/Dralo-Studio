"""Explicit BGM selection with independent asset and episode revision guards."""

# ruff: noqa: RUF001
from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import Episode, EpisodeProduction, MediaFile, ProjectAssetLink
from app.services import (
    asset_production_service,
    canvas_generation_service,
    media_service,
    project_audio_library,
)


async def apply(session, project, asset_id, payload):
    await canvas_generation_service.submission_lock(session, project)
    link = await asset_production_service.ensure_active_link(session, project.id, asset_id)
    await session.execute(update(ProjectAssetLink).where(ProjectAssetLink.id == link.id)
        .values(production_revision=ProjectAssetLink.production_revision))
    await session.refresh(link)
    if link.production_archived:
        raise ConflictError("资产已归档，请先恢复")
    if link.production_revision != payload.expected_asset_revision:
        raise ConflictError("资产采用版本或资料已变化，请刷新后重新选择")
    result = await project_audio_library.library(session, project)
    chosen = next((item for item in result["items"] if item.get("assetId") == asset_id
        and item.get("versionId") == payload.version_id and item["category"] == "BGM" and item["available"]), None)
    if chosen is None:
        raise ConflictError("仅能使用当前项目已采用、可播放的配乐版本")
    episode = await session.get(Episode, payload.episode_id)
    if episode is None or episode.project_id != project.id:
        raise ConflictError("目标分集不属于当前项目")
    await session.execute(update(Episode).where(Episode.id == episode.id).values(script_revision=Episode.script_revision))
    production = await session.scalar(select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
        .execution_options(populate_existing=True))
    revision = production.revision if production else 0
    if revision != payload.expected_episode_revision:
        raise ConflictError("分集设置已变化，请刷新后重新确认配乐")
    current = dict(production.settings or {}) if production else {}
    if current.get("background_music") is False:
        raise ConflictError("本集已禁用背景音乐，请先在声音策略中允许背景音乐")
    current.update(background_audio_media_id=chosen["mediaId"], background_audio_volume=payload.volume)
    if production:
        changed = await session.execute(update(EpisodeProduction).where(EpisodeProduction.id == production.id,
            EpisodeProduction.revision == revision).values(settings=current, revision=revision+1))
        if changed.rowcount != 1:
            raise ConflictError("分集设置已变化，请刷新后重新确认")
    else:
        session.add(EpisodeProduction(episode_id=episode.id, settings=current, revision=1))
    await media_service.link_media_to_project(session, await session.get(MediaFile, chosen["mediaId"]), project)
    await session.flush()
    return {"episode_id":episode.id, "revision":revision+1, "media_file_id":chosen["mediaId"], "volume":payload.volume}
