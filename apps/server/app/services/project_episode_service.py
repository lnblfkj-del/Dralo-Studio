"""Episode CRUD and production summary services."""

from typing import Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Asset,
    AssetUsage,
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    MediaFile,
    Project,
    ProjectAssetLink,
    ProjectMediaLink,
    ProviderModel,
    Scene,
    SegmentVideoVersion,
    Shot,
    ShotVideoVersion,
    VideoSegment,
)

async def list_episodes(session: AsyncSession, project_id: int) -> list[Episode]:
    stmt = (
        select(Episode)
        .where(Episode.project_id == project_id, Episode.status != "archived")
        .order_by(Episode.number.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_episode(
    session: AsyncSession, project_id: int, episode_id: int
) -> Episode:
    episode = await session.get(Episode, episode_id)
    if episode is None or episode.project_id != project_id:
        raise NotFoundError("分集不存在")
    if episode.status == "archived":
        raise ConflictError("本集已归档，请从分集大纲恢复后再操作；原成果已保留")
    return episode


async def create_episode(
    session: AsyncSession, project: Project, data: dict[str, Any]
) -> Episode:
    number = data["number"]
    exists = await session.execute(
        select(Episode.id).where(
            Episode.project_id == project.id, Episode.number == number
        )
    )
    if exists.scalar_one_or_none() is not None:
        raise ConflictError(f"第 {number} 集已存在")

    episode = Episode(project_id=project.id, owner_id=project.owner_id, **data)
    session.add(episode)
    await session.flush()
    session.add(EpisodeProduction(episode_id=episode.id, settings={}))
    await session.flush()
    from app.services.script_finalization_service import invalidate_project_structure

    await invalidate_project_structure(
        session, project.id, f"新增第 {episode.number} 集，全集结构已变化"
    )
    return episode


_EPISODE_PRODUCTION_DEFAULTS: dict[str, Any] = {
    "aspect_ratio": "project",
    "resolution": "720p",
    "frame_rate": 24,
    "default_shot_duration": 4,
    "include_subtitles": True,
    "background_audio_media_id": None,
    "background_audio_volume": 0.3,
    "video_model_id": None,
    "asset_ids": [],
    "dialogue_cues": [],
    "sound_cues": [],
}


def _episode_workflow_status(
    episode: Episode,
    *,
    shot_count: int,
    ready_shot_count: int,
    failed_shot_count: int,
    generating_shot_count: int,
    final_media_file_id: int | None,
    operation_status: str | None = None,
) -> str:
    if final_media_file_id is not None:
        return "completed"
    if not (episode.script or "").strip():
        return "script_missing"
    if shot_count == 0:
        return "script_ready"
    if operation_status in {"queued", "running", "processing", "downloading", "retrying"}:
        return "producing"
    if operation_status == "failed":
        return "failed"
    if ready_shot_count >= shot_count:
        return "clips_ready"
    if failed_shot_count and not generating_shot_count:
        return "failed"
    if ready_shot_count or generating_shot_count:
        return "producing"
    return "storyboard_ready"


async def list_episode_productions(
    session: AsyncSession, project_id: int
) -> list[dict[str, Any]]:
    """一次性返回分集制作读模型，避免列表页逐集、逐场景请求。"""
    episodes = await list_episodes(session, project_id)
    if not episodes:
        return []
    episode_ids = [episode.id for episode in episodes]
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    scene_counts = dict((await session.execute(
        select(Scene.episode_id, func.count(Scene.id))
        .where(Scene.episode_id.in_(episode_ids))
        .group_by(Scene.episode_id)
    )).all())
    shot_stats = {
        row.episode_id: row
        for row in (await session.execute(
            select(
                Scene.episode_id.label("episode_id"),
                func.count(Shot.id).label("shot_count"),
                func.coalesce(func.sum(Shot.duration), 0).label("duration"),
                func.coalesce(func.sum(case((Shot.status == "failed", 1), else_=0)), 0).label("failed_count"),
                func.coalesce(func.sum(case((Shot.status == "generating", 1), else_=0)), 0).label("generating_count"),
            )
            .join(Shot, Shot.scene_id == Scene.id)
            .where(
                Scene.episode_id.in_(episode_ids),
                Shot.status != SHOT_STATUS_SUPERSEDED,
            )
            .group_by(Scene.episode_id)
        )).all()
    }
    ready_counts = dict((await session.execute(
        select(Scene.episode_id, func.count(func.distinct(ShotVideoVersion.shot_id)))
        .select_from(ShotVideoVersion)
        .join(Shot, Shot.id == ShotVideoVersion.shot_id)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(
            Scene.episode_id.in_(episode_ids),
            ShotVideoVersion.is_final.is_(True),
            Shot.status != SHOT_STATUS_SUPERSEDED,
        )
        .group_by(Scene.episode_id)
    )).all())
    asset_counts = dict((await session.execute(
        select(AssetUsage.episode_id, func.count(func.distinct(AssetUsage.asset_id)))
        .where(AssetUsage.episode_id.in_(episode_ids))
        .group_by(AssetUsage.episode_id)
    )).all())
    profiles = {
        item.episode_id: item for item in (await session.execute(
            select(EpisodeProduction).where(EpisodeProduction.episode_id.in_(episode_ids))
        )).scalars()
    }
    active_plan_ids = [item.active_plan_id for item in profiles.values() if item.active_plan_id]
    active_plans = {
        item.id: item for item in (await session.execute(
            select(EpisodeProductionPlan).where(EpisodeProductionPlan.id.in_(active_plan_ids))
        )).scalars()
    } if active_plan_ids else {}
    segment_stats = {
        row.plan_id: row
        for row in (await session.execute(
            select(
                VideoSegment.plan_id.label("plan_id"),
                func.count(VideoSegment.id).label("segment_count"),
                func.coalesce(func.sum(VideoSegment.timeline_duration), 0).label("timeline_duration"),
                func.coalesce(func.sum(VideoSegment.generation_duration), 0).label("generation_duration"),
                func.coalesce(func.sum(case((VideoSegment.status == "failed", 1), else_=0)), 0).label("failed_count"),
                func.coalesce(func.sum(case((VideoSegment.status == "generating", 1), else_=0)), 0).label("generating_count"),
            )
            .where(VideoSegment.plan_id.in_(active_plan_ids))
            .group_by(VideoSegment.plan_id)
        )).all()
    } if active_plan_ids else {}
    ready_segment_counts = dict((await session.execute(
        select(VideoSegment.plan_id, func.count(func.distinct(SegmentVideoVersion.segment_id)))
        .select_from(SegmentVideoVersion)
        .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
        .where(
            VideoSegment.plan_id.in_(active_plan_ids),
            SegmentVideoVersion.is_final.is_(True),
        )
        .group_by(VideoSegment.plan_id)
    )).all()) if active_plan_ids else {}
    operation_rows = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    Job.project_id == project_id,
                    Job.target_type.in_(["episode_video_batch", "episode_export"]),
                    Job.target_id.in_(episode_ids),
                )
                .order_by(Job.id.desc())
            )
        ).all()
    )
    latest_operations: dict[int, Job] = {}
    for operation in operation_rows:
        if operation.target_id is not None:
            latest_operations.setdefault(operation.target_id, operation)

    summaries: list[dict[str, Any]] = []
    for episode in episodes:
        profile = profiles.get(episode.id)
        shots = shot_stats.get(episode.id)
        shot_count = int(shots.shot_count if shots else 0)
        ready_count = int(ready_counts.get(episode.id, 0))
        failed_count = int(shots.failed_count if shots else 0)
        generating_count = int(shots.generating_count if shots else 0)
        active_plan = active_plans.get(profile.active_plan_id) if profile else None
        segment_row = segment_stats.get(active_plan.id) if active_plan else None
        segment_count = int(segment_row.segment_count if segment_row else 0)
        ready_segment_count = int(ready_segment_counts.get(active_plan.id, 0)) if active_plan else 0
        failed_segment_count = int(segment_row.failed_count if segment_row else 0)
        generating_segment_count = int(segment_row.generating_count if segment_row else 0)
        effective_count = segment_count if active_plan else shot_count
        effective_ready = ready_segment_count if active_plan else ready_count
        effective_failed = failed_segment_count if active_plan else failed_count
        effective_generating = generating_segment_count if active_plan else generating_count
        final_media_id = profile.final_media_file_id if profile else None
        operation = latest_operations.get(episode.id)
        settings = {**_EPISODE_PRODUCTION_DEFAULTS, **(profile.settings if profile else {})}
        summaries.append({
            "episode": episode,
            "production_id": profile.id if profile else None,
            "workflow_status": _episode_workflow_status(
                episode,
                shot_count=effective_count,
                ready_shot_count=effective_ready,
                failed_shot_count=effective_failed,
                generating_shot_count=effective_generating,
                final_media_file_id=final_media_id,
                operation_status=operation.status if operation else None,
            ),
            "settings": settings,
            "revision": profile.revision if profile else 0,
            "source_script_revision": profile.source_script_revision if profile else None,
            "script_dependency_status": (
                "stale"
                if profile and profile.script_stale
                else "current"
                if episode.finalized_script_revision == episode.script_revision
                and episode.finalized_script_revision is not None
                else "unconfirmed"
            ),
            "script_stale_reason": profile.script_stale_reason if profile else None,
            "scene_count": int(scene_counts.get(episode.id, 0)),
            "shot_count": shot_count,
            # Compatibility plans are a one-shot/one-segment migration view.
            # Keep legacy counters meaningful there while confirmed E5 plans
            # expose segment facts only through the dedicated fields.
            "ready_shot_count": (
                effective_ready
                if active_plan and active_plan.status == "compatibility"
                else ready_count
            ),
            "failed_shot_count": (
                effective_failed
                if active_plan and active_plan.status == "compatibility"
                else failed_count
            ),
            "production_plan_id": active_plan.id if active_plan else None,
            "production_plan_version": active_plan.version if active_plan else None,
            "production_plan_status": active_plan.status if active_plan else None,
            "segment_count": segment_count,
            "ready_segment_count": ready_segment_count,
            "failed_segment_count": failed_segment_count,
            "duration": float(segment_row.timeline_duration if segment_row else shots.duration if shots else 0),
            "generation_duration": float(segment_row.generation_duration if segment_row else 0),
            "asset_count": max(
                int(asset_counts.get(episode.id, 0)),
                len(settings.get("asset_ids") or []),
            ),
            "final_media_file_id": final_media_id,
            "final_media_url": f"/api/media/{final_media_id}" if final_media_id else None,
            "active_job_id": operation.id if operation else None,
            "active_job_status": operation.status if operation else None,
            "active_job_progress": operation.progress if operation else 0,
            "active_job_result": operation.result if operation else None,
            "last_error": (
                (operation.error_message if operation else None)
                or (profile.last_error if profile else None)
            ),
            "can_retry": bool(
                operation and operation.status in {"failed", "cancelled"}
            ),
        })
    return summaries


async def get_episode_production(
    session: AsyncSession, episode: Episode
) -> dict[str, Any]:
    summaries = await list_episode_productions(session, episode.project_id)
    for summary in summaries:
        if summary["episode"].id == episode.id:
            return summary
    raise NotFoundError("分集制作数据不存在")


async def update_episode_production(
    session: AsyncSession,
    episode: Episode,
    *,
    settings: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    """以乐观锁保存制作设置；媒体生成和状态变化不走此入口。"""
    from app.services.production_dialogue_service import validate_dialogue_cues, validate_sound_cues

    await validate_dialogue_cues(session, episode, settings.get("dialogue_cues") or [])
    await validate_sound_cues(session, episode, settings.get("sound_cues") or [])
    audio_id = settings.get("background_audio_media_id")
    if audio_id is not None:
        media = await session.get(MediaFile, audio_id)
        from app.services.team_access import same_team
        if media is None or not await same_team(session, media.owner_id, episode.owner_id) or media.kind != "audio":
            raise ConflictError("配乐素材不存在、类型错误或无权使用")
        if media.project_id != episode.project_id:
            linked = await session.scalar(select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == media.id,
            ))
            if linked is None:
                raise ConflictError("配乐素材尚未加入当前项目")

    video_model_id = settings.get("video_model_id")
    if video_model_id is not None:
        video_model = await session.get(ProviderModel, video_model_id)
        if (
            video_model is None
            or not video_model.enabled
            or video_model.model_type != "video"
        ):
            raise ConflictError("保存的视频模型不存在、已停用或类型不正确")
        resolution = str(settings.get("resolution") or "").lower()
        supported_resolutions = {
            str(item).lower()
            for item in (video_model.default_params or {}).get("resolutions", [])
            if str(item).strip()
        }
        if resolution != "project" and (
            not supported_resolutions or resolution not in supported_resolutions
        ):
            raise ConflictError("当前视频模型不支持所选清晰度，请重新选择或配置模型能力")

    asset_ids = {int(asset_id) for asset_id in settings.get("asset_ids") or []}
    if asset_ids:
        found_asset_ids = set((await session.execute(
            select(Asset.id)
            .outerjoin(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
            .where(
                Asset.id.in_(asset_ids),
                or_(Asset.project_id == episode.project_id, ProjectAssetLink.project_id == episode.project_id),
            )
        )).scalars())
        if found_asset_ids != asset_ids:
            raise ConflictError("本集引用了不存在或尚未加入项目的资产")

    profile = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if profile is None:
        if expected_revision != 0:
            raise ConflictError("制作设置已变化，请刷新后重试")
        profile = EpisodeProduction(episode_id=episode.id, settings=settings, revision=1)
        session.add(profile)
        await session.flush()
    else:
        result = await session.execute(
            update(EpisodeProduction)
            .where(EpisodeProduction.id == profile.id, EpisodeProduction.revision == expected_revision)
            .values(settings=settings, revision=expected_revision + 1)
        )
        if result.rowcount != 1:
            raise ConflictError("制作设置已变化，请刷新后重试")
        await session.flush()
    return await get_episode_production(session, episode)
