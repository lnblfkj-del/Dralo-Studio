"""Production overlay reads and relationship guards."""

from app.services.asset_production_shared import *
from app.schemas.production_contract import AssetProductionContext

def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()

async def version_is_adopted(
    session: AsyncSession, asset_id: int, version_id: int
) -> bool:
    links = (
        await session.scalars(
            select(ProjectAssetLink).where(ProjectAssetLink.asset_id == asset_id)
        )
    ).all()
    return any(
        choice.get("version_id") == version_id
        for link in links
        for choice in (link.production_data or {}).get("adoptions", [])
    )

async def link_for(session: AsyncSession, project_id: int, asset_id: int) -> ProjectAssetLink:
    link = await session.scalar(
        select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == asset_id
        )
    )
    if link is None:
        raise NotFoundError("资产未加入当前项目")
    return link

async def ensure_active_link(
    session: AsyncSession, project_id: int, asset_id: int
) -> ProjectAssetLink:
    link = await link_for(session, project_id, asset_id)
    if link.production_archived:
        raise ConflictError("资产已归档，请先恢复后再修改制作资料或素材")
    return link

def _node_label(node: CanvasNode) -> str:
    data = node.data or {}
    for key in ("title", "name", "label"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return node.node_type

async def _source_outputs(
    session: AsyncSession, project: Project, raw_sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    canvas = await session.scalar(
        select(CanvasDocument).where(CanvasDocument.project_id == project.id)
    )
    for raw in raw_sources:
        source = ProductionSource.model_validate(raw)
        episode = await session.get(Episode, source.episode_id) if source.episode_id else None
        scene = await session.get(Scene, source.scene_id) if source.scene_id else None
        shot = await session.get(Shot, source.shot_id) if source.shot_id else None
        segment = await session.get(VideoSegment, source.segment_id) if source.segment_id else None
        node = (
            await session.scalar(
                select(CanvasNode).where(
                    CanvasNode.canvas_id == canvas.id,
                    CanvasNode.node_key == source.canvas_node_key,
                )
            )
            if canvas is not None and source.canvas_node_key
            else None
        )
        missing = (
            (source.episode_id is not None and (episode is None or episode.project_id != project.id))
            or (source.scene_id is not None and (scene is None or scene.episode_id != source.episode_id))
            or (source.shot_id is not None and (shot is None or shot.scene_id != source.scene_id))
            or (source.segment_id is not None and (segment is None or segment.episode_id != source.episode_id))
            or (source.canvas_node_key is not None and node is None)
        )
        stale = bool(
            not missing
            and episode is not None
            and source.script_revision is not None
            and source.script_revision < episode.script_revision
        )
        structured = any(
            value is not None
            for value in (
                source.episode_id,
                source.scene_id,
                source.shot_id,
                source.segment_id,
                source.canvas_node_key,
            )
        )
        status = "missing" if missing else "stale" if stale else "valid" if structured else "legacy"
        parts: list[str] = []
        if episode is not None and episode.project_id == project.id:
            parts.append(f"第 {episode.number} 集{f' · {episode.title}' if episode.title else ''}")
        if scene is not None and scene.episode_id == source.episode_id:
            parts.append(f"场景 {scene.order + 1} · {scene.name}")
        if shot is not None and shot.scene_id == source.scene_id:
            parts.append(f"分镜 {shot.order + 1}")
        if segment is not None and segment.episode_id == source.episode_id:
            parts.append(f"片段 {segment.order + 1}{f' · {segment.title}' if segment.title else ''}")
        if node is not None:
            parts.append(f"画布节点 · {_node_label(node)}")
        if not parts and source.locator:
            parts.append(source.locator)
        if not parts:
            parts.append("项目级来源")
        outputs.append(
            {
                **source.model_dump(mode="json"),
                "status": status,
                "display_path": " / ".join(parts),
                "episode_number": episode.number if episode and episode.project_id == project.id else None,
                "episode_title": episode.title if episode and episode.project_id == project.id else None,
                "current_script_revision": episode.script_revision if episode and episode.project_id == project.id else None,
                "scene_order": scene.order if scene and scene.episode_id == source.episode_id else None,
                "scene_name": scene.name if scene and scene.episode_id == source.episode_id else None,
                "shot_order": shot.order if shot and shot.scene_id == source.scene_id else None,
                "segment_order": segment.order if segment and segment.episode_id == source.episode_id else None,
                "segment_title": segment.title if segment and segment.episode_id == source.episode_id else None,
                "canvas_node_label": _node_label(node) if node else None,
            }
        )
    return outputs

async def _archive_impact(
    session: AsyncSession, project: Project, asset_id: int, data: dict[str, Any]
) -> dict[str, int]:
    usage_count = int(
        await session.scalar(
            select(func.count(AssetUsage.id)).where(
                AssetUsage.project_id == project.id, AssetUsage.asset_id == asset_id
            )
        )
        or 0
    )
    dependent_asset_count = int(
        await session.scalar(
            select(func.count(ProjectAssetLink.id)).where(
                ProjectAssetLink.project_id == project.id,
                ProjectAssetLink.production_data["profile"]["character_asset_id"].as_integer()
                == asset_id,
            )
        )
        or 0
    )
    snapshots = (
        await session.scalars(
            select(SegmentScriptSnapshot)
            .join(VideoSegment, VideoSegment.id == SegmentScriptSnapshot.segment_id)
            .join(Episode, Episode.id == VideoSegment.episode_id)
            .where(Episode.project_id == project.id)
        )
    ).all()
    historical_snapshot_count = sum(
        1
        for snapshot in snapshots
        if any(
            item.get("asset_id") == asset_id
            for item in (snapshot.content.get("refs") or {}).get("asset_bindings", [])
        )
    )
    return {
        "usage_count": usage_count,
        "adoption_count": len(data.get("adoptions", [])),
        "historical_snapshot_count": historical_snapshot_count,
        "dependent_asset_count": dependent_asset_count,
    }

async def read_production(
    session: AsyncSession,
    project: Project,
    asset_id: int,
    *,
    segment_id: int | None = None,
    scene_ids: list[int] | None = None,
    include_archive_impact: bool = True,
) -> dict[str, Any]:
    link = await link_for(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    data = link.production_data or {}
    profile = data.get("profile")
    if profile is None:
        # Read-only compatibility: never infer audio purpose or mark AI guesses confirmed.
        attrs = asset.attributes or {}
        profile = {
            key: str(attrs[key])
            for key in ("age", "appearance", "costume", "voice")
            if attrs.get(key) is not None and isinstance(attrs[key], (str, int))
        }
    from app.services.asset_audio_profile import effective_profile
    profile = effective_profile(asset, {**data, "profile": profile})
    adoptions = data.get("adoptions")
    if adoptions is None:
        legacy = await session.scalar(
            select(AssetVersion)
            .where(
                AssetVersion.asset_id == asset_id,
                AssetVersion.is_final.is_(True),
                AssetVersion.review_status != "archived",
                AssetVersion.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE,
            )
            .order_by(AssetVersion.version.desc())
            .limit(1)
        )
        media = await session.get(MediaFile, legacy.media_file_id) if legacy else None
        adoptions = (
            [
                {
                    "key": "default",
                    "version_id": legacy.id,
                    "media_file_id": media.id,
                    "kind": media.kind,
                    "origin": "legacy_final",
                }
            ]
            if legacy and media
            else []
        )
    scope_overrides = data.get("scope_overrides", [])
    if not isinstance(scope_overrides, list):
        scope_overrides = []
    effective_profile = AssetProfile.model_validate(profile).model_dump(mode="json")
    effective_prompt = data.get("prompt_anchor", asset.prompt_anchor)
    effective_description = None
    effective_scope_keys: list[str] = []
    target_keys = [*(f"scene:{scene_id}" for scene_id in (scene_ids or []))]
    if segment_id is not None:
        target_keys.append(f"segment:{segment_id}")
    for key in target_keys:
        override = next(
            (
                item
                for item in scope_overrides
                if f"{item.get('target_type')}:{item.get('target_id')}" == key
            ),
            None,
        )
        if override is None:
            continue
        effective_scope_keys.append(key)
        if override.get("description") is not None:
            effective_description = override["description"]
        if override.get("prompt_anchor") is not None:
            effective_prompt = override["prompt_anchor"]
        effective_profile = {
            **effective_profile,
            **(override.get("profile") or {}),
        }
    output_type = AssetProductionOut if include_archive_impact else AssetProductionContext
    archive = {"archive_impact": await _archive_impact(session, project, asset_id, data)} if include_archive_impact else {}
    return output_type(
        asset_id=asset_id,
        project_id=project.id,
        revision=link.production_revision,
        archived=link.production_archived,
        prompt_anchor=effective_prompt,
        profile=AssetProfile.model_validate(effective_profile),
        sources=await _source_outputs(session, project, data.get("sources", [])),
        adoptions=adoptions,
        scope_overrides=scope_overrides,
        effective_scope_keys=effective_scope_keys,
        effective_description=effective_description,
        **archive,
        readiness="archived"
        if link.production_archived
        else "reference_selected"
        if adoptions
        else "missing_media",
        readiness_reasons=["project_archived"]
        if link.production_archived
        else ["provider_preflight_required"]
        if adoptions
        else ["no_adopted_media"],
    ).model_dump(mode="json")
