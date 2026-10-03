"""Core plan access, reference validation and legacy migration."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    Episode,
    EpisodeProduction,
    EpisodeProductionPlan,
    MediaFile,
    Project,
    ProjectMediaLink,
    Scene,
    SegmentVideoVersion,
    Shot,
    ShotVideoVersion,
    VideoSegment,
    VideoSegmentShot,
)
from app.services import asset_service
from app.services.asset_binding_service import binding_role, resolve_asset_binding
from app.services.segment_plan_read_service import serialize_plan
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED


async def _production(session: AsyncSession, episode: Episode) -> EpisodeProduction:
    item = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if item is None:
        item = EpisodeProduction(episode_id=episode.id, settings={})
        session.add(item)
        await session.flush()
    return item


async def _episode_shots(session: AsyncSession, episode_id: int) -> list[Shot]:
    return list(
        (
            await session.scalars(
                select(Shot)
                .join(Scene, Scene.id == Shot.scene_id)
                .where(Scene.episode_id == episode_id, Shot.status != SHOT_STATUS_SUPERSEDED)
                .order_by(Scene.order, Scene.id, Shot.order, Shot.id)
            )
        ).all()
    )


async def _episode_scenes(session: AsyncSession, episode_id: int) -> list[Scene]:
    return list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.episode_id == episode_id)
                .order_by(Scene.order, Scene.id)
            )
        ).all()
    )


async def _validated_refs(
    session: AsyncSession,
    episode: Episode,
    refs: dict[str, Any],
) -> dict[str, Any]:
    """Canonicalize E4 asset-view references and reject foreign/stale media IDs."""
    if not isinstance(refs, dict):
        raise ValidationError("片段资产引用必须是对象")
    raw_bindings = refs.get("asset_bindings", [])
    raw_media_ids = refs.get("reference_media_ids", [])
    raw_unresolved = refs.get("unresolved_assets", [])
    raw_continuity = refs.get("continuity")
    if not isinstance(raw_bindings, list) or not isinstance(raw_media_ids, list) or not isinstance(raw_unresolved, list):
        raise ValidationError("片段资产引用结构无效")
    canonical: list[dict[str, Any]] = []
    canonical_media: list[int] = []
    project = await session.get(Project, episode.project_id)
    if project is None:
        raise NotFoundError("项目不存在")
    for raw in raw_bindings:
        if not isinstance(raw, dict):
            raise ValidationError("资产视图绑定格式无效")
        try:
            asset_id = int(raw["asset_id"])
            version_id = int(raw["asset_version_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("资产视图绑定缺少资产或版本编号") from exc
        role = binding_role(raw)
        binding = await resolve_asset_binding(
            session,
            project,
            asset_id=asset_id,
            adoption_key=raw.get("adoption_key"),
            asset_version_id=version_id,
            media_file_id=raw.get("media_file_id"),
            role=role,
        )
        canonical.append({
            **raw,
            **binding,
        })
        if binding["media_kind"] == "image" and binding["media_file_id"] not in canonical_media:
            canonical_media.append(binding["media_file_id"])
    for value in raw_media_ids:
        try:
            media_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("参考图片编号无效") from exc
        media = await session.get(MediaFile, media_id)
        from app.services.team_access import same_team
        if media is None or not await same_team(session, media.owner_id, episode.owner_id) or media.kind != "image":
            raise ValidationError("参考图片不存在或无权访问")
        belongs_to_project = media.project_id == episode.project_id or await session.scalar(
            select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == media.id,
            )
        ) is not None
        bound_by_asset = media.id in canonical_media
        if not belongs_to_project and not bound_by_asset:
            raise ValidationError("参考图片未加入当前项目")
        if media.id not in canonical_media:
            canonical_media.append(media.id)
    unresolved: list[dict[str, Any]] = []
    for raw in raw_unresolved:
        if not isinstance(raw, dict) or not raw.get("asset_id"):
            raise ValidationError("待绑定资产结构无效")
        asset = await asset_service.get_asset(session, episode.project_id, int(raw["asset_id"]))
        if raw.get("role") in {"first_frame", "last_frame"} or (
            (asset.attributes or {}).get("derived_type") == "segment_first_frame"
        ):
            continue
        unresolved.append({
            **raw,
            "asset_id": asset.id,
            "asset_name": asset.name,
            "asset_type": asset.asset_type,
            "resolved": False,
        })
    return {
        **refs,
        "asset_bindings": canonical,
        "reference_media_ids": canonical_media,
        "unresolved_assets": unresolved,
        "continuity": (
            {
                "source_lineage_key": str(raw_continuity.get("source_lineage_key") or "").strip(),
                "role": "first_frame",
            }
            if isinstance(raw_continuity, dict)
            and str(raw_continuity.get("source_lineage_key") or "").strip()
            else None
        ),
    }


async def get_active_plan(session: AsyncSession, episode: Episode) -> dict[str, Any]:
    production = await _production(session, episode)
    if production.active_plan_id is None:
        raise NotFoundError("本集尚未建立片段生产计划")
    plan = await session.get(EpisodeProductionPlan, production.active_plan_id)
    if plan is None or plan.episode_id != episode.id:
        raise NotFoundError("本集片段生产计划不存在")
    return await serialize_plan(session, plan)


async def select_segment_video_version(
    session: AsyncSession,
    episode: Episode,
    segment_id: int,
    version_id: int,
    *,
    expected_plan_revision: int,
    expected_input_fingerprint: str,
) -> SegmentVideoVersion:
    """Select the only version eligible for episode composition."""
    production = await _production(session, episode)
    segment = await session.scalar(
        select(VideoSegment).where(
            VideoSegment.id == segment_id,
            VideoSegment.episode_id == episode.id,
            VideoSegment.plan_id == production.active_plan_id,
        )
    )
    if segment is None:
        raise NotFoundError("当前生产计划中不存在该视频片段")
    plan = await session.get(EpisodeProductionPlan, segment.plan_id)
    if plan is None or plan.revision != expected_plan_revision:
        raise ConflictError("片段计划已变化，请刷新候选后重新采用")
    version = await session.scalar(
        select(SegmentVideoVersion).where(
            SegmentVideoVersion.id == version_id,
            SegmentVideoVersion.segment_id == segment.id,
        )
    )
    if version is None:
        raise NotFoundError("片段视频版本不存在")
    from app.services import media_service
    from app.services.segment_video_candidate_service import candidate_evidence

    media = await session.get(MediaFile, version.media_file_id)
    if media is not None:
        await media_service.ensure_media_metadata(session, media)
    evidence = await candidate_evidence(
        session, segment=segment, plan=plan, version=version
    )
    if evidence["input_fingerprint"] != expected_input_fingerprint:
        raise ConflictError("候选输入快照已变化，请刷新后重新采用")
    if version.is_final and evidence["status"] == "adopted":
        return version
    if not evidence["adoptable"]:
        raise ConflictError(evidence["reason"] or "当前候选不可采用")
    await session.execute(
        update(SegmentVideoVersion)
        .where(SegmentVideoVersion.segment_id == segment.id)
        .values(is_final=False)
    )
    version.is_final = True
    segment.status = "ready"
    await session.flush()
    return version


async def initialize_legacy_plan(session: AsyncSession, episode: Episode) -> dict[str, Any]:
    """为迁移后新增的旧结构数据显式建立一分镜一片段兼容计划。"""
    production = await _production(session, episode)
    if production.active_plan_id is not None:
        plan = await session.get(EpisodeProductionPlan, production.active_plan_id)
        if plan is not None:
            return await serialize_plan(session, plan)
    shots = await _episode_shots(session, episode.id)
    if not shots:
        raise ConflictError("请先完成场景与分镜拆解，再建立片段计划")
    durations = [float(shot.duration or 4) for shot in shots]
    plan = EpisodeProductionPlan(
        episode_id=episode.id,
        owner_id=episode.owner_id,
        version=1,
        source_type="legacy",
        status="compatibility",
        source_script_revision=episode.script_revision,
        model_capability_snapshot={},
        parameters={"source": "legacy_shot_video"},
        total_timeline_duration=sum(durations),
        total_generation_duration=sum(durations),
    )
    session.add(plan)
    await session.flush()
    production.active_plan_id = plan.id
    for order, (shot, duration) in enumerate(zip(shots, durations, strict=True), start=1):
        old_versions = list(
            (
                await session.scalars(
                    select(ShotVideoVersion)
                    .where(ShotVideoVersion.shot_id == shot.id)
                    .order_by(ShotVideoVersion.version)
                )
            ).all()
        )
        segment = VideoSegment(
            plan_id=plan.id,
            episode_id=episode.id,
            order=order,
            lineage_key=f"legacy-segment-{shot.id}",
            parent_lineage_keys=[],
            title=f"片段 {order:02d}",
            generation_duration=duration,
            timeline_duration=duration,
            prompt=shot.prompt or shot.action or "待补充片段提示词",
            negative_prompt=shot.negative_prompt,
            parameters={"legacy_compatibility": True},
            refs=shot.refs or {},
            status="ready" if any(item.is_final for item in old_versions) else shot.status,
        )
        session.add(segment)
        await session.flush()
        session.add(
            VideoSegmentShot(
                segment_id=segment.id,
                shot_id=shot.id,
                order=1,
                start_time=0,
                end_time=duration,
            )
        )
        for old in old_versions:
            session.add(
                SegmentVideoVersion(
                    segment_id=segment.id,
                    media_file_id=old.media_file_id,
                    source_job_id=old.source_job_id,
                    legacy_shot_video_version_id=old.id,
                    version=old.version,
                    prompt=old.prompt,
                    negative_prompt=old.negative_prompt,
                    parameters=old.parameters or {},
                    is_final=old.is_final,
                )
            )
    await session.flush()
    return await serialize_plan(session, plan)


async def mirror_legacy_shot_version(
    session: AsyncSession, shot: Shot, version: ShotVideoVersion
) -> SegmentVideoVersion | None:
    """Keep compatibility plans current while E5 has not switched job targets."""
    row = (
        await session.execute(
            select(VideoSegment, EpisodeProductionPlan)
            .join(VideoSegmentShot, VideoSegmentShot.segment_id == VideoSegment.id)
            .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
            .join(
                EpisodeProduction,
                EpisodeProduction.active_plan_id == EpisodeProductionPlan.id,
            )
            .where(
                VideoSegmentShot.shot_id == shot.id,
                EpisodeProductionPlan.status == "compatibility",
            )
        )
    ).one_or_none()
    if row is None:
        return None
    segment, _plan = row
    link_count = int(
        await session.scalar(
            select(func.count(VideoSegmentShot.id)).where(VideoSegmentShot.segment_id == segment.id)
        )
        or 0
    )
    if link_count != 1:
        return None
    mirrored = await session.scalar(
        select(SegmentVideoVersion).where(
            SegmentVideoVersion.legacy_shot_video_version_id == version.id
        )
    )
    if mirrored is None:
        mirrored = SegmentVideoVersion(
            segment_id=segment.id,
            media_file_id=version.media_file_id,
            source_job_id=version.source_job_id,
            legacy_shot_video_version_id=version.id,
            version=version.version,
            prompt=version.prompt,
            negative_prompt=version.negative_prompt,
            parameters=version.parameters or {},
            is_final=version.is_final,
        )
        session.add(mirrored)
    if version.is_final:
        await session.execute(
            update(SegmentVideoVersion)
            .where(
                SegmentVideoVersion.segment_id == segment.id,
                SegmentVideoVersion.legacy_shot_video_version_id != version.id,
            )
            .values(is_final=False)
        )
        mirrored.is_final = True
        segment.status = "ready"
    await session.flush()
    return mirrored
