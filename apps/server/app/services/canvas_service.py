"""M4/M6D canvas snapshots and structured production projections."""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select, update
from app.services.team_access import owner_scope, same_team
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.storage_safety import write_storage_bytes
from app.core.media_quota import require_media_budget
from app.models import (
    Asset,
    AssetVersion,
    AssetUsage,
    CanvasDocument,
    CanvasEdge,
    CanvasNode,
    Episode,
    EpisodeProduction,
    Project,
    ProjectAssetLink,
    MediaFile,
    Job,
    ProjectMediaLink,
    Scene,
    SegmentVideoVersion,
    Shot,
    VideoSegment,
)
from app.schemas.canvas import CanvasSave


async def get_document(session: AsyncSession, project_id: int) -> CanvasDocument | None:
    return await session.scalar(
        select(CanvasDocument).where(CanvasDocument.project_id == project_id)
    )


async def get_canvas_node(
    session: AsyncSession, project_id: int, node_key: str
) -> CanvasNode:
    node = await session.scalar(
        select(CanvasNode)
        .join(CanvasDocument, CanvasDocument.id == CanvasNode.canvas_id)
        .where(CanvasDocument.project_id == project_id, CanvasNode.node_key == node_key)
    )
    if node is None:
        raise NotFoundError("画布节点不存在，请先等待画布保存")
    return node


async def finalize_image_job(session: AsyncSession, job, result: dict) -> dict:
    if job.target_type != "canvas_node" or job.target_id is None or job.project_id is None:
        raise ConflictError("图片任务缺少画布节点关联")
    node = await session.get(CanvasNode, job.target_id)
    if node is None or node.node_type not in {"image", "character", "scene", "costume", "prop"}:
        raise NotFoundError("图片画布节点不存在")
    await validate_job_node(session, job, node)
    previous = next((v for v in node.data.get("media_versions", []) if v.get("job_id") == job.id), None)
    if previous:
        return {"canvas_node_id": node.node_key, "media_file_id": previous["media_id"]}
    data = result.get("image_bytes")
    if not isinstance(data, bytes):
        raise ConflictError("图片任务没有返回有效文件")
    from app.services.asset_service import image_format
    extension, mime_type = image_format(data)
    # Node keys can be supplied by Agent plans; never use them as filesystem paths.
    relative_path = Path("projects") / str(job.project_id) / "canvas" / str(node.id) / f"{uuid4().hex}.{extension}"
    absolute_path = settings.storage_path / relative_path
    await require_media_budget(session, len(data))
    write_storage_bytes(settings, absolute_path, data)
    media = MediaFile(
        project_id=job.project_id,
        owner_id=job.owner_id,
        kind="image",
        source="generation",
        file_path=relative_path.as_posix(),
        original_name=absolute_path.name,
        mime_type=mime_type,
        size=len(data),
        hash=sha256(data).hexdigest(),
    )
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
    node.data = {
        **(node.data or {}),
        "generation_status": "succeeded",
        "job_id": job.id,
        "prompt": job.payload.get("prompt"),
        "parameters": job.payload.get("parameters", {}),
    }
    await record_job_media_version(session, job, node, media.id)
    if node.node_type in {"character", "scene", "costume", "prop"}:
        asset = await session.get(Asset, node.data.get("production_asset_id"))
        if not asset or asset.owner_id != job.owner_id or asset.asset_type != node.node_type or asset.project_id != job.project_id:
            raise ConflictError("共享视觉资产不可写，生成结果未绑定")
        profile = dict((asset.attributes or {}).get("canvas_profile") or {})
        views = list(profile.get("views") or [])
        if not any(view.get("media_id") == media.id for view in views):
            views.append({"media_id": media.id, "label": "AI 生成候选"})
        primary = profile.get("primary_media_id")
        profile.update({
            "revision": int(profile.get("revision", 0)) + 1,
            "views": views,
            "primary_media_id": primary or media.id,
            "voice_media_id": profile.get("voice_media_id"),
            "speech_preset": profile.get("speech_preset"),
        })
        asset.attributes = {**(asset.attributes or {}), "canvas_profile": profile}
        versions = (await session.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id))).all()
        if not any(version.media_file_id == media.id for version in versions):
            session.add(AssetVersion(
                asset_id=asset.id, media_file_id=media.id,
                source_job_id=job.id,
                version=max((version.version for version in versions), default=0) + 1,
                prompt=job.payload.get("prompt"), parameters=job.payload.get("parameters", {}),
                view_label="AI 生成候选", view_type="base", review_status="candidate",
                tags=["画布生成"], is_final=not bool(primary),
            ))
    await session.flush()
    return {
        "canvas_node_id": node.node_key,
        "media_file_id": media.id,
        "media_url": f"/api/media/{media.id}",
        "revised_prompt": result.get("revised_prompt"),
        **({"advanced_processing": {key: job.payload["advanced"][key] for key in ("version", "tool", "output", "origin_key", "source_media_id")}} if job.payload.get("advanced") else {}),
    }


async def finalize_video_node_job(session: AsyncSession, job, result: dict) -> dict:
    """Attach a persisted generated video to its canvas node."""
    if job.target_type != "canvas_node" or job.target_id is None:
        raise ConflictError("视频任务缺少画布节点关联")
    node = await session.get(CanvasNode, job.target_id)
    if node is None or node.node_type != "video":
        raise NotFoundError("视频画布节点不存在")
    await validate_job_node(session, job, node)
    media_id = result.get("media_file_id")
    if not isinstance(media_id, int):
        raise ConflictError("视频任务没有返回有效媒体")
    node.data = {
        **(node.data or {}),
        "generation_status": "succeeded",
        "job_id": job.id,
        "prompt": job.payload.get("prompt"),
        "parameters": job.payload.get("parameters", {}),
    }
    await record_job_media_version(session, job, node, media_id)
    await session.flush()
    return {**result, "canvas_node_id": node.node_key}


async def finalize_audio_node_job(session, job, result):
    node = await session.get(CanvasNode, job.target_id)
    if not node or node.node_type not in {"audio", "voice"}:
        raise NotFoundError("配音目标节点不存在")
    await validate_job_node(session, job, node)
    prior = next((v for v in node.data.get("media_versions", []) if v.get("job_id") == job.id), None)
    if prior:
        return {"media_file_id": prior["media_id"], "canvas_node_id": node.node_key, "kind": "audio"}
    data = result.get("audio_bytes")
    if not isinstance(data, bytes) or not data or len(data) > 50 * 1024 * 1024:
        raise ConflictError("配音结果为空或超过 50 MB")
    relative = Path("projects") / str(job.project_id) / "audio" / f"{uuid4().hex}.mp3"
    path = settings.storage_path / relative
    await require_media_budget(session, len(data))
    write_storage_bytes(settings, path, data)
    media = MediaFile(owner_id=job.owner_id, project_id=job.project_id, kind="audio", source="generation",
        file_path=relative.as_posix(), original_name=path.name, mime_type="audio/mpeg", size=len(data), hash=sha256(data).hexdigest())
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
    node.data = {**node.data, "job_id": job.id, "generation_status": "succeeded", "parameters": job.payload.get("parameters", {})}
    await record_job_media_version(session, job, node, media.id)
    if node.node_type == "voice":
        asset = await session.get(Asset, node.data.get("production_asset_id"))
        if not asset or asset.owner_id != job.owner_id or asset.asset_type != "voice" or asset.project_id != job.project_id:
            raise ConflictError("声音资产不可写，生成结果未绑定")
        profile = dict((asset.attributes or {}).get("canvas_profile") or {})
        views = list(profile.get("views") or [])
        if not any(view.get("media_id") == media.id for view in views):
            views.append({"media_id": media.id, "label": "AI 生成候选"})
        primary = profile.get("primary_media_id")
        profile.update({
            "revision": int(profile.get("revision", 0)) + 1,
            "views": views,
            "primary_media_id": primary or media.id,
            "voice_media_id": None,
            "speech_preset": profile.get("speech_preset"),
        })
        asset.attributes = {**(asset.attributes or {}), "canvas_profile": profile}
        versions = (await session.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id))).all()
        if not any(version.media_file_id == media.id for version in versions):
            session.add(AssetVersion(
                asset_id=asset.id, media_file_id=media.id,
                version=max((version.version for version in versions), default=0) + 1,
                prompt=job.payload.get("prompt") or "声音生成", parameters=job.payload.get("parameters", {}),
                view_label="AI 生成候选", view_type="base", review_status="candidate",
                tags=["画布生成"], is_final=not bool(primary),
            ))
    await session.flush()
    return {"media_file_id": media.id, "canvas_node_id": node.node_key, "kind": "audio", "media_url": f"/api/media/{media.id}"}


async def assert_node_unlocked(session, project, node):
    seen = set()
    while node and node.node_key not in seen:
        if node.locked:
            raise ConflictError("节点或所属分组已锁定，请先解锁")
        seen.add(node.node_key)
        node = await get_canvas_node(session, project.id, node.parent_key) if node.parent_key else None


async def validate_job_node(session, job, node):
    document = await session.get(CanvasDocument, node.canvas_id)
    source_key = job.payload.get("parameters", {}).get("source_node_key")
    if not document or document.project_id != job.project_id or document.owner_id != job.owner_id or (source_key and source_key != node.node_key):
        raise NotFoundError("任务原始节点已删除或替换，结果不能写入其他节点")


async def record_job_media_version(session, job, node, media_id):
    active_media = node.data.get("media_id")
    project = await session.get(Project, job.project_id)
    locked = False
    try:
        await assert_node_unlocked(session, project, node)
    except ConflictError:
        locked = True
    record_media_version(node, job.id, media_id)
    if locked:
        node.data = {**node.data, "media_id": active_media, "pending_media_id": media_id}


def record_media_version(node, job_id: int, media_id: int):
    data = dict(node.data or {})
    versions = list(data.get("media_versions", []))
    if not any(item.get("job_id") == job_id for item in versions):
        if data.get("media_id") and not versions:
            versions.append({"media_id": data["media_id"], "job_id": None})
        versions.append({"media_id": media_id, "job_id": job_id})
    data["media_versions"] = versions
    if data.get("media_id") and data["media_id"] != media_id:
        data["pending_media_id"] = media_id
    else:
        data["media_id"] = media_id
    node.data = data


async def select_media_version(session, project, node_key, media_id, expected_revision=None):
    await guard_media_revision(session, project, expected_revision)
    node = await get_canvas_node(session, project.id, node_key)
    await assert_node_unlocked(session, project, node)
    if node.data.get("production_asset_id"):
        raise ConflictError("请在共享资产编辑器中选择主素材")
    if node.locked:
        raise ConflictError("请先解锁节点")
    if not any(v.get("media_id") == media_id for v in node.data.get("media_versions", [])):
        raise NotFoundError("该素材不属于此节点的版本")
    media = await session.get(MediaFile, media_id)
    if not media or not await same_team(session, media.owner_id, project.owner_id) or node.node_type in {"image", "video", "audio"} and node.node_type != media.kind:
        raise NotFoundError("媒体不存在")
    selected = next(v for v in node.data["media_versions"] if v.get("media_id") == media_id)
    data = {**node.data, "media_id": media_id, "pending_media_id": None}
    if selected.get("director_origin"):
        data["director_origin"] = selected["director_origin"]
    elif media_id != node.data.get("media_id"):
        data.pop("director_origin", None)
    node.data = data
    await session.flush()
    return await get_snapshot(session, project)


async def guard_media_revision(session, project, expected_revision):
    document = await get_document(session, project.id)
    if document:
        from app.services.canvas_production_service import guard_revision
        await guard_revision(session, project, document.revision if expected_revision is None else expected_revision)


async def attach_media(session, project, node_key, media_id, expected_revision=None):
    await guard_media_revision(session, project, expected_revision)
    node = await get_canvas_node(session, project.id, node_key)
    await assert_node_unlocked(session, project, node)
    if node.data.get("production_asset_id"):
        raise ConflictError("请在共享资产编辑器中绑定素材，避免覆盖共享设定")
    media = await session.get(MediaFile, media_id)
    if not media or not await same_team(session, media.owner_id, project.owner_id):
        raise NotFoundError("素材不存在或无权访问")
    if node.locked or node.node_type not in {media.kind, "character", "scene", "costume", "prop", "voice", "asset", "file"}:
        raise ConflictError("节点已锁定或素材类型不匹配")
    versions = list(node.data.get("media_versions", []))
    if node.data.get("media_id") and not versions:
        versions.append({"media_id": node.data["media_id"], "job_id": None})
    if not any(v["media_id"] == media_id for v in versions):
        versions.append({"media_id": media_id, "job_id": None})
    node.data = {**node.data, "media_id": media_id, "media_versions": versions, "pending_media_id": None}
    if node.node_type == "file" and media.kind == "audio":
        node.node_type = "audio"
    await session.flush()
    return await get_snapshot(session, project)


async def get_business_projection(session: AsyncSession, project: Project) -> dict:
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    episodes = list((await session.execute(
        select(Episode).where(Episode.project_id == project.id, Episode.status != "archived").order_by(Episode.number)
    )).scalars())
    episode_ids = [episode.id for episode in episodes]
    scenes = list((await session.execute(
        select(Scene).where(Scene.episode_id.in_(episode_ids)).order_by(Scene.episode_id, Scene.order)
    )).scalars()) if episode_ids else []
    scene_ids = [scene.id for scene in scenes]
    shots = list((await session.execute(
        select(Shot).where(
            Shot.scene_id.in_(scene_ids), Shot.status != SHOT_STATUS_SUPERSEDED
        ).order_by(Shot.scene_id, Shot.order)
    )).scalars()) if scene_ids else []
    segments = list((await session.execute(
        select(VideoSegment)
        .join(
            EpisodeProduction,
            EpisodeProduction.active_plan_id == VideoSegment.plan_id,
        )
        .where(
            VideoSegment.episode_id.in_(episode_ids),
            EpisodeProduction.episode_id == VideoSegment.episode_id,
        )
        .order_by(VideoSegment.episode_id, VideoSegment.order)
    )).scalars()) if episode_ids else []
    segment_ids = [segment.id for segment in segments]
    segment_versions = list((await session.execute(
        select(SegmentVideoVersion)
        .where(SegmentVideoVersion.segment_id.in_(segment_ids))
        .order_by(SegmentVideoVersion.segment_id, SegmentVideoVersion.version)
    )).scalars()) if segment_ids else []
    versions_by_segment: dict[int, list[SegmentVideoVersion]] = {
        segment_id: [] for segment_id in segment_ids
    }
    for version in segment_versions:
        versions_by_segment[version.segment_id].append(version)
    assets = list((await session.execute(
        select(Asset)
        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
        .where(ProjectAssetLink.project_id == project.id)
        .order_by(Asset.asset_type, Asset.name)
    )).scalars())
    usages = list((await session.execute(
        select(AssetUsage).where(AssetUsage.project_id == project.id)
    )).scalars())
    document = await get_document(session, project.id)
    represented_asset_ids: set[int] = set()
    if document is not None:
        canvas_nodes = (await session.scalars(
            select(CanvasNode).where(CanvasNode.canvas_id == document.id)
        )).all()
        for canvas_node in canvas_nodes:
            asset_id = (canvas_node.data or {}).get("production_asset_id")
            if not (canvas_node.data or {}).get("projected") and type(asset_id) is int:
                represented_asset_ids.add(asset_id)

    nodes = []
    edges = []
    # M6D.5：shot 只带 scene_id，需要 scene → episode 映射才能生成完整深链。
    scene_episode = {scene.id: scene.episode_id for scene in scenes}
    for episode in episodes:
        key = f"episode:{episode.id}"
        nodes.append({
            "key": key, "node_type": "episode", "entity_type": "episode",
            "entity_id": episode.id, "title": f"第 {episode.number} 集 · {episode.title or '未命名'}",
            "content": episode.synopsis or "暂无分集梗概", "status": episode.status,
            "episode_id": episode.id,
        })
    for scene in scenes:
        key = f"scene:{scene.id}"
        parent = f"episode:{scene.episode_id}"
        nodes.append({
            "key": key, "node_type": "scene", "entity_type": "scene",
            "entity_id": scene.id, "parent_key": parent, "title": scene.name,
            "content": " · ".join(filter(None, [scene.location, scene.time_of_day, scene.description])) or "暂无场景描述",
            "episode_id": scene.episode_id, "scene_id": scene.id,
        })
        edges.append({"key": f"contains:{parent}:{key}", "source": parent, "target": key, "relation": "contains"})
    for shot in shots:
        key = f"shot:{shot.id}"
        parent = f"scene:{shot.scene_id}"
        nodes.append({
            "key": key, "node_type": "shot", "entity_type": "shot",
            "entity_id": shot.id, "parent_key": parent,
            "title": f"分镜 {shot.order} · {shot.shot_size or '景别待定'}",
            "content": shot.action or shot.dialogue or "暂无分镜内容", "status": shot.status,
            "episode_id": scene_episode.get(shot.scene_id), "scene_id": shot.scene_id,
        })
        edges.append({"key": f"contains:{parent}:{key}", "source": parent, "target": key, "relation": "contains"})
    for segment in segments:
        key = f"segment:{segment.id}"
        parent = f"episode:{segment.episode_id}"
        versions = versions_by_segment[segment.id]
        adopted = next((version for version in versions if version.is_final), None)
        status = (
            "archived"
            if segment.status == "archived"
            else "adopted"
            if adopted is not None
            else "candidate"
            if versions
            else segment.status
        )
        nodes.append({
            "key": key,
            "node_type": "segment",
            "entity_type": "segment",
            "entity_id": segment.id,
            "parent_key": parent,
            "title": f"片段 {segment.order:02d} · {segment.title or '未命名'}",
            "content": segment.prompt or "暂无片段脚本",
            "status": status,
            "episode_id": segment.episode_id,
            "segment_id": segment.id,
            "candidate_count": len(versions),
            "adopted_version_id": adopted.id if adopted else None,
            "media_id": adopted.media_file_id if adopted else None,
            "duration_seconds": segment.timeline_duration,
        })
        edges.append({
            "key": f"contains:{parent}:{key}",
            "source": parent,
            "target": key,
            "relation": "contains",
        })
    for asset in assets:
        # A hand-created character/scene production card is the canonical canvas
        # representation of its asset. Returning another generic asset card makes
        # the correct scene look like it was created with the wrong node type.
        if asset.id in represented_asset_ids:
            continue
        key = f"asset:{asset.id}"
        nodes.append({
            "key": key, "node_type": "asset", "entity_type": "asset",
            "entity_id": asset.id, "title": asset.name,
            "content": asset.prompt_anchor or asset.description or f"{asset.asset_type} 资产",
            "asset_type": asset.asset_type,
        })
    for usage in usages:
        source = f"asset:{usage.asset_id}"
        target = f"shot:{usage.shot_id}"
        edges.append({
            "key": f"uses:{usage.id}", "source": source, "target": target,
            "relation": "uses",
        })
    return {"project_id": project.id, "nodes": nodes, "edges": edges}


async def get_snapshot(session: AsyncSession, project: Project) -> dict:
    document = await get_document(session, project.id)
    if document is None:
        return {
            "project_id": project.id,
            "revision": 0,
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "nodes": [],
            "edges": [],
            "updated_at": None,
        }
    nodes = list((await session.execute(
        select(CanvasNode).where(CanvasNode.canvas_id == document.id).order_by(CanvasNode.id)
    )).scalars())
    from app.services.canvas_production_service import enrich
    production = await enrich(session, project, nodes)
    from app.services.canvas_multitrack_service import enrich as enrich_edits
    edits = await enrich_edits(session, project, nodes)
    for key, value in edits.items():
        production[key] = {**production.get(key, {}), **value}
    edges = list((await session.execute(
        select(CanvasEdge).where(CanvasEdge.canvas_id == document.id).order_by(CanvasEdge.id)
    )).scalars())
    job_ids = [node.data.get("job_id") for node in nodes if node.data.get("job_id")]
    jobs = {job.id: job for job in (await session.scalars(select(Job).where(
        Job.id.in_(job_ids), Job.project_id == project.id, Job.owner_id == project.owner_id,
    ))).all()} if job_ids else {}
    # Migrate legacy file nodes only when stored media metadata proves it is audio.
    legacy_ids = [node.data.get("media_id") for node in nodes if node.node_type == "file"]
    audio_ids = set((await session.scalars(select(MediaFile.id).where(
        MediaFile.id.in_(legacy_ids), MediaFile.kind == "audio", owner_scope(MediaFile.owner_id, project.owner_id),
    ))).all()) if legacy_ids else set()
    return {
        "project_id": project.id,
        "revision": document.revision,
        "viewport": document.viewport,
        "nodes": [{
            "id": node.node_key,
            "type": "audio" if node.node_type == "file" and node.data.get("media_id") in audio_ids else node.node_type,
            "x": node.x,
            "y": node.y,
            "width": node.width,
            "height": node.height,
            "z_index": node.z_index,
            "parent_id": node.parent_key,
            "data": {**node.data, **production.get(node.node_key, {}), **({"generation_status": jobs[node.data["job_id"]].status} if node.data.get("job_id") in jobs else {})},
            "locked": node.locked,
        } for node in nodes],
        "edges": [{
            "id": edge.edge_key,
            "source": edge.source_key,
            "target": edge.target_key,
            "source_handle": edge.source_handle,
            "target_handle": edge.target_handle,
            "data": edge.data,
        } for edge in edges],
        "updated_at": document.updated_at,
    }


async def save_snapshot(
    session: AsyncSession, project: Project, payload: CanvasSave
) -> dict:
    document = await get_document(session, project.id)
    if document is None:
        if payload.expected_revision != 0:
            raise ConflictError("画布已在其他页面更新，请刷新后重试")
        document = CanvasDocument(
            project_id=project.id,
            owner_id=project.owner_id,
            revision=1,
            viewport=payload.viewport.model_dump(),
        )
        session.add(document)
        await session.flush()
    else:
        result = await session.execute(
            update(CanvasDocument)
            .where(
                CanvasDocument.id == document.id,
                CanvasDocument.revision == payload.expected_revision,
            )
            .values(
                revision=CanvasDocument.revision + 1,
                viewport=payload.viewport.model_dump(),
            )
        )
        if result.rowcount != 1:
            raise ConflictError("画布已在其他页面更新，请刷新后重试")
        document.revision = payload.expected_revision + 1
        document.viewport = payload.viewport.model_dump()
        await session.execute(delete(CanvasEdge).where(CanvasEdge.canvas_id == document.id))
    # Stable database IDs are essential: in-flight jobs point at CanvasNode.id.
    existing = {node.node_key: node for node in (await session.scalars(
        select(CanvasNode).where(CanvasNode.canvas_id == document.id)
    )).all()}
    incoming = {item.id: item for item in payload.nodes}
    from app.services.canvas_multitrack_service import validate_inputs
    await validate_inputs(session, project, payload)
    for key, previous in existing.items():
        current, seen, protected = previous, set(), False
        while current and current.node_key not in seen:
            protected = protected or current.locked
            seen.add(current.node_key)
            current = existing.get(current.parent_key)
        if not protected:
            continue
        item = incoming.get(key)
        if item is None or (item.type, item.x, item.y, item.parent_id) != (previous.node_type, previous.x, previous.y, previous.parent_key):
            raise ConflictError("锁定的节点或分组不能被修改或删除，请先解锁并保存")
        if previous.node_type == "text" and any(item.data.get(field, "") != previous.data.get(field, "") for field in ("title", "content")):
            raise ConflictError("锁定文本不能被覆盖，请先解锁并保存")
        if previous.node_type == "multitrack" and item.data.get("edit_project_id") != previous.data.get("edit_project_id"):
            raise ConflictError("Locked edit entry cannot be rebound")
    for item in payload.nodes:
        if item.type == "text" and any(not isinstance(item.data.get(field, ""), str) or len(item.data.get(field, "")) > limit for field, limit in (("title", 255), ("content", 100_000))):
            raise ConflictError("文本标题或正文格式错误/超过长度限制")
        node = existing.pop(item.id, None)
        is_new = node is None
        if node is None:
            node = CanvasNode(canvas_id=document.id, node_key=item.id)
            session.add(node)
        data = dict(item.data)
        if item.type == "multitrack":
            from app.services.canvas_multitrack_service import validate_binding
            await validate_binding(session, project, data)
        # Director documents are only writable through validated, revisioned APIs.
        data.pop("director_document", None)
        data.pop("upstream_director_document", None)
        data.pop("upstream_director_outputs", None)
        if (node.data or {}).get("upstream_director_outputs"):
            data["upstream_director_outputs"] = node.data["upstream_director_outputs"]
        if (node.data or {}).get("upstream_director_document"):
            if item.type != "director":
                raise ConflictError("有工程数据的导演台不能改变节点类型")
            data["upstream_director_document"] = node.data["upstream_director_document"]
        data.pop("director_origin", None)
        # Clipboard/undo may restore a new node with a saved scene. Validate it
        # like the director API, and never copy request IDs or capture ownership.
        if is_new and item.type == "director" and item.data.get("director_document"):
            from pydantic import ValidationError
            from app.schemas.canvas_director import DirectorState
            try:
                restored = DirectorState.model_validate(item.data["director_document"].get("state"))
            except (ValidationError, AttributeError) as exc:
                raise ConflictError("复制或恢复的 3D 工程格式无效") from exc
            restored_state = restored.model_dump(mode="json", exclude_none=True)
            restored_state["project"].update(activeCameraId=restored.project.activeCameraId, panoramaAssetId=None)
            data["director_document"] = {"revision": 1, "state": restored_state, "requests": {}, "captures": {}}
        if (node.data or {}).get("director_origin"):
            data["director_origin"] = node.data["director_origin"]
        if (node.data or {}).get("director_document"):
            if item.type != "director":
                raise ConflictError("有工程数据的导演台不能改变节点类型")
            data["director_document"] = node.data["director_document"]
        # Job metadata is server-owned. A stale autosave must not erase a result.
        for key in ("job_id", "generation_status", "media_versions", "pending_media_id", "parameters", "prompt", "processing_origin", "advanced_origin", "advanced_tool"):
            if key in (node.data or {}):
                data[key] = node.data[key]
        if (node.data or {}).get("media_versions"):
            data["media_id"] = node.data.get("media_id")
        node.node_type = item.type
        node.x, node.y = item.x, item.y
        node.width, node.height = item.width, item.height
        node.z_index, node.parent_key = item.z_index, item.parent_id
        from app.services.canvas_production_service import ensure_entity
        data = await ensure_entity(session, project, node, data)
        data.pop("production_profile", None)  # Canonical entity data is never written by autosave.
        data.pop("production_readonly", None)
        node.data, node.locked = data, item.locked
    for node in existing.values():
        await session.delete(node)
    session.add_all([CanvasEdge(
        canvas_id=document.id,
        edge_key=edge.id,
        source_key=edge.source,
        target_key=edge.target,
        source_handle=edge.source_handle,
        target_handle=edge.target_handle,
        data=edge.data,
    ) for edge in payload.edges])
    await session.flush()
    return await get_snapshot(session, project)
