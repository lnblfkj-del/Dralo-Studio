"""Asset production catalog, version and usage read contracts."""

from app.services.asset_production_shared import *
from app.services.asset_production_core import link_for
from app.services.asset_audio_profile import audio_usage_expression, effective_profile


def _source_episode_numbers(
    production_data: dict[str, Any] | None,
) -> set[int]:
    raw_numbers = (production_data or {}).get("script_scope", {}).get(
        "episode_numbers", []
    )
    return {
        int(value)
        for value in raw_numbers
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit())
    }

async def catalog(
    session: AsyncSession,
    project: Project,
    *,
    page: int,
    page_size: int,
    kind: str | None,
    q: str,
    episode_id: int | None,
    unassigned: bool,
    archived: bool,
    readiness: str | None,
    subtype: str | None,
    sort: str,
) -> dict[str, Any]:
    base = (
        select(Asset, ProjectAssetLink)
        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
        .where(
            ProjectAssetLink.project_id == project.id,
            ProjectAssetLink.production_archived.is_(archived),
        )
    )
    counts = dict(
        (
            await session.execute(
                select(Asset.asset_type, func.count())
                .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
                .where(
                    ProjectAssetLink.project_id == project.id,
                    ProjectAssetLink.production_archived.is_(False),
                )
                .group_by(Asset.asset_type)
            )
        ).all()
    )
    if kind:
        base = base.where(Asset.asset_type == kind)
    if subtype:
        profile = ProjectAssetLink.production_data["profile"]
        field = "audio_usage" if kind == "voice" else "character_role"
        subtype_value = profile[field].as_string()
        if kind == "voice":
            subtype_value = audio_usage_expression()
        base = base.where(
            or_(subtype_value == subtype, subtype_value.is_(None))
            if subtype == "unclassified"
            else subtype_value == subtype
        )
    if q:
        # Extract string values: searching serialized JSON misses escaped Chinese aliases.
        aliases = [
            ProjectAssetLink.production_data["profile"]["aliases"][index]
            .as_string()
            .contains(q, autoescape=True)
            for index in range(30)
        ]
        base = base.where(
            or_(
                Asset.name.contains(q, autoescape=True),
                Asset.description.contains(q, autoescape=True),
                *aliases,
            )
        )
    # Script asset breakdown stores source episode numbers on the asset. That
    # is a source association, not a fake shot-level adoption, so catalog
    # filters must recognize it alongside precise AssetUsage rows.
    episode_attribute_rows = (
        await session.execute(
            select(Asset.id, ProjectAssetLink.production_data)
            .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
            .where(
                ProjectAssetLink.project_id == project.id,
                ProjectAssetLink.production_archived.is_(False),
            )
        )
    ).all()
    episode_attribute_ids: set[int] = set()
    for asset_id, production_data in episode_attribute_rows:
        if _source_episode_numbers(production_data):
            episode_attribute_ids.add(asset_id)
    usage = select(AssetUsage.id).where(
        AssetUsage.asset_id == Asset.id, AssetUsage.project_id == project.id
    )
    if episode_id is not None:
        episode = await session.get(Episode, episode_id)
        if episode is None or episode.project_id != project.id:
            raise NotFoundError("分集不存在")
        selected_attribute_ids = {
            asset_id
            for asset_id, production_data in episode_attribute_rows
            if episode.number in _source_episode_numbers(production_data)
        }
        base = base.where(
            or_(
                exists(usage.where(AssetUsage.episode_id == episode_id)),
                Asset.id.in_(selected_attribute_ids or {-1}),
            )
        )
    if unassigned:
        base = base.where(~exists(usage))
        if episode_attribute_ids:
            base = base.where(Asset.id.notin_(episode_attribute_ids))
    adoption_path = ProjectAssetLink.production_data["adoptions"]
    legacy_final = exists(
        select(AssetVersion.id).where(
            AssetVersion.asset_id == Asset.id,
            AssetVersion.is_final.is_(True),
            AssetVersion.review_status != "archived",
            AssetVersion.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE,
        )
    )
    adoption_count = func.coalesce(func.json_array_length(adoption_path), 0)
    explicit_adoption = adoption_count > 0
    if readiness == "ready":
        base = base.where(or_(explicit_adoption, legacy_final))
    elif readiness == "missing_media":
        base = base.where(adoption_count == 0, ~legacy_final)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    order = {
        "updated_asc": Asset.updated_at.asc(),
        "name_asc": Asset.name.asc(),
        "name_desc": Asset.name.desc(),
    }.get(sort, Asset.updated_at.desc())
    rows = (
        await session.execute(
            base.order_by(order, Asset.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    ids = [asset.id for asset, _ in rows]
    version_counts = (
        dict(
            (
                await session.execute(
                    select(AssetVersion.asset_id, func.count())
                    .where(AssetVersion.asset_id.in_(ids))
                    .group_by(AssetVersion.asset_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    # Only recover task identity/state, not payloads, prompts, provider secrets or full histories.
    latest = (
        select(Job.target_id, func.max(Job.id).label("last_id"))
        .where(
            Job.project_id == project.id,
            Job.target_type == "asset",
            Job.target_id.in_(ids),
            Job.deleted_at.is_(None),
        )
        .group_by(Job.target_id)
        .subquery()
    )
    jobs = (
        (await session.scalars(select(Job).join(latest, Job.id == latest.c.last_id))).all()
        if ids
        else []
    )
    job_map = {
        job.target_id: {"id": job.id, "status": job.status, "progress": job.progress}
        for job in jobs
    }
    usage_counts = (
        dict(
            (
                await session.execute(
                    select(AssetUsage.asset_id, func.count())
                    .where(AssetUsage.project_id == project.id, AssetUsage.asset_id.in_(ids))
                    .group_by(AssetUsage.asset_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    adopted_version_ids = {
        item.get("version_id")
        for _, link in rows
        for item in (link.production_data or {}).get("adoptions", [])
        if item.get("version_id")
    }
    legacy_assets = [asset.id for asset, link in rows if "adoptions" not in (link.production_data or {})]
    preview_rows = (
        (
            await session.execute(
                select(AssetVersion, MediaFile)
                .join(MediaFile, MediaFile.id == AssetVersion.media_file_id)
                .where(
                    AssetVersion.asset_id.in_(ids),
                    or_(
                        MediaFile.kind == "image",
                        AssetVersion.id.in_(adopted_version_ids or {-1}),
                        AssetVersion.asset_id.in_(legacy_assets or [-1]) & AssetVersion.is_final.is_(True),
                    ),
                    AssetVersion.review_status != "archived",
                    AssetVersion.view_type != asset_service.LAYOUT_SHEET_VIEW_TYPE,
                )
                .order_by(AssetVersion.version.desc())
            )
        ).all()
        if ids
        else []
    )
    previews: dict[int, dict[str, Any]] = {}
    ready_ids: set[int] = set()
    for version, media in preview_rows:
        if version.id in adopted_version_ids or (version.asset_id in legacy_assets and version.is_final):
            ready_ids.add(version.asset_id)
        previews.setdefault(
            version.asset_id,
            {
                "version_id": version.id,
                "media_file_id": media.id,
                "kind": media.kind,
                "mime_type": media.mime_type,
                "width": media.width,
                "height": media.height,
                "duration_seconds": media.duration,
            },
        )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "counts": counts,
        "items": [
            {
                "id": asset.id,
                "name": asset.name,
                "slug": link.local_slug,
                "asset_type": asset.asset_type,
                "revision": link.production_revision,
                "archived": link.production_archived,
                "version_count": version_counts.get(asset.id, 0),
                "description": asset.description,
                "prompt_anchor": (
                    link.production_data["prompt_anchor"]
                    if "prompt_anchor" in (link.production_data or {})
                    else asset.prompt_anchor
                ),
                "updated_at": asset.updated_at.isoformat(),
                "profile": effective_profile(asset, link.production_data),
                "readiness": "ready" if asset.id in ready_ids else "missing_media",
                "usage_count": usage_counts.get(asset.id, 0),
                "preview_media": previews.get(asset.id),
                "latest_job": job_map.get(asset.id),
            }
            for asset, link in rows
        ],
    }

async def version_page(
    session: AsyncSession, project: Project, asset_id: int, page: int, page_size: int
) -> dict[str, Any]:
    await link_for(session, project.id, asset_id)
    total = await session.scalar(
        select(func.count()).select_from(AssetVersion).where(AssetVersion.asset_id == asset_id)
    )
    rows = (
        await session.execute(
            select(AssetVersion, MediaFile)
            .join(MediaFile, MediaFile.id == AssetVersion.media_file_id)
            .where(AssetVersion.asset_id == asset_id)
            .order_by(AssetVersion.version.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": version.id,
                "version": version.version,
                "view_label": version.view_label,
                "view_type": version.view_type,
                "review_status": version.review_status,
                "tags": version.tags,
                "is_final": version.is_final,
                "needs_split": version.view_type == asset_service.LAYOUT_SHEET_VIEW_TYPE,
                "media": {
                    "id": media.id,
                    "kind": media.kind,
                    "mime_type": media.mime_type,
                    "width": media.width,
                    "height": media.height,
                    "duration_seconds": media.duration,
                    "size": media.size,
                    "hash": media.hash,
                },
            }
            for version, media in rows
        ],
    }

async def guard_delete(session: AsyncSession, asset_id: int, version_id: int | None = None) -> None:
    usages = select(AssetUsage.id).where(AssetUsage.asset_id == asset_id)
    if version_id is not None:
        usages = usages.where(
            or_(AssetUsage.asset_version_id == version_id, AssetUsage.asset_version_id.is_(None))
        )
    if await session.scalar(usages.limit(1)):
        raise ConflictError("资产已被使用，请先处理引用或改为项目内归档")
    links = (
        await session.scalars(select(ProjectAssetLink).where(ProjectAssetLink.asset_id == asset_id))
    ).all()
    if any(
        any(
            version_id is None or item["version_id"] == version_id
            for item in (link.production_data or {}).get("adoptions", [])
        )
        for link in links
    ):
        raise ConflictError("资产版本已被项目采用，请使用归档保留历史")
    if version_id is None and await session.scalar(
        select(ProjectAssetLink.id)
        .where(
            ProjectAssetLink.production_data["profile"]["character_asset_id"].as_integer()
            == asset_id,
        )
        .limit(1)
    ):
        raise ConflictError("角色仍被造型或声音资料关联，请先处理引用")
    # Snapshots are immutable historical references; never silently detach them by hard delete.
    for snapshot in (await session.scalars(select(SegmentScriptSnapshot))).all():
        bindings = (snapshot.content.get("refs") or {}).get("asset_bindings", [])
        if any(
            item.get("asset_id") == asset_id
            and (version_id is None or item.get("asset_version_id") == version_id)
            for item in bindings
        ):
            raise ConflictError("版本被片段历史引用，不能删除")

async def usage_page(
    session: AsyncSession, project: Project, asset_id: int, page: int, page_size: int
) -> dict[str, Any]:
    await link_for(session, project.id, asset_id)
    query = select(AssetUsage).where(
        AssetUsage.project_id == project.id, AssetUsage.asset_id == asset_id
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(
            query.add_columns(
                Episode.number,
                Episode.title,
                Scene.order,
                Scene.name,
                Shot.order,
                AssetVersion.version,
                AssetVersion.view_label,
            )
            .outerjoin(Episode, Episode.id == AssetUsage.episode_id)
            .outerjoin(Scene, Scene.id == AssetUsage.scene_id)
            .outerjoin(Shot, Shot.id == AssetUsage.shot_id)
            .outerjoin(AssetVersion, AssetVersion.id == AssetUsage.asset_version_id)
            .order_by(AssetUsage.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                "id": item.id,
                "episode_id": item.episode_id,
                "scene_id": item.scene_id,
                "shot_id": item.shot_id,
                "asset_version_id": item.asset_version_id,
                "usage_type": item.usage_type,
                "episode_number": episode_number,
                "episode_title": episode_title,
                "scene_order": scene_order,
                "scene_name": scene_name,
                "shot_order": shot_order,
                "asset_version": asset_version,
                "asset_version_label": asset_version_label,
                "source_status": "valid"
                if episode_number is not None and scene_order is not None and shot_order is not None
                else "missing",
            }
            for (
                item,
                episode_number,
                episode_title,
                scene_order,
                scene_name,
                shot_order,
                asset_version,
                asset_version_label,
            ) in rows
        ],
    }
