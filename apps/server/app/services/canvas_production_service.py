"""C2-A entities reuse Asset identity; canvas cards are references, not copies."""
# ruff: noqa: RUF001
import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, update
from app.services.team_access import owner_scope, same_team

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Asset,
    AssetUsage,
    AssetVersion,
    CanvasDocument,
    CanvasNode,
    Episode,
    EpisodeProduction,
    MediaFile,
    ProjectAssetLink,
    Scene,
    VideoSegment,
)
from app.services import asset_production_service, asset_service, canvas_service
from app.schemas.production_contract import AssetProductionPatch, AssetScopeOverride

PRODUCTION_ENTITY_KINDS = {"character", "scene", "costume", "prop", "voice"}
VISUAL_ENTITY_KINDS = PRODUCTION_ENTITY_KINDS - {"voice"}


def entity_token(asset, link=None):
    production = [link.production_revision, link.production_data] if link is not None else None
    return sha256(json.dumps(
        [asset.name, asset.description, asset.attributes, production],
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()


async def owned_asset(session, project, asset_id, kind):
    asset = await session.get(Asset, asset_id) if type(asset_id) is int else None
    if not asset or not await same_team(session, asset.owner_id, project.owner_id) or asset.asset_type != kind:
        raise NotFoundError("共享资产不存在、类型不符或无权访问")
    return asset


async def ensure_entity(session, project, node, data):
    if node.node_type not in PRODUCTION_ENTITY_KINDS or data.get("projected"):
        return data
    asset_id = (node.data or {}).get("production_asset_id") or data.get("production_asset_id")
    if asset_id:
        asset = await owned_asset(session, project, asset_id, node.node_type)
        link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == project.id,
            ProjectAssetLink.asset_id == asset.id,
        ))
        if link is None:
            base = (asset.slug or f"asset-{asset.id}")[:110]
            alias = base
            suffix = 2
            while await session.scalar(select(ProjectAssetLink.id).where(
                ProjectAssetLink.project_id == project.id,
                ProjectAssetLink.local_slug == alias,
            )):
                alias = f"{base[:110-len(str(suffix))]}-{suffix}"
                suffix += 1
            session.add(ProjectAssetLink(
                project_id=project.id,
                asset_id=asset.id,
                local_slug=alias,
            ))
            await session.flush()
    else:
        media_id = data.get("media_id")
        media = await session.get(MediaFile, media_id) if type(media_id) is int else None
        expected_kind = "audio" if node.node_type == "voice" else "image"
        media_id = media.id if media and await same_team(session, media.owner_id, project.owner_id) and media.kind == expected_kind else None
        asset = await asset_service.create_asset(session, project, {
            "asset_type": node.node_type, "name": str(data.get("title") or "未命名")[:255],
            "slug": "canvas-" + uuid4().hex, "description": str(data.get("content") or "")[:4000],
            "attributes": {"canvas_profile": {"revision": 0,
                "views": [{"media_id": media_id, "label": "原有素材"}] if media_id else [],
                "primary_media_id": media_id, "voice_media_id": None}},
        })
    return {**data, "production_asset_id": asset.id}


async def enrich(session, project, nodes):
    ids = [n.data.get("production_asset_id") for n in nodes if n.data.get("production_asset_id")]
    assets = {a.id: a for a in (await session.scalars(select(Asset).where(
        Asset.id.in_(ids), owner_scope(Asset.owner_id, project.owner_id),
    ))).all()} if ids else {}
    links = {link.asset_id: link for link in (await session.scalars(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id.in_(ids),
    ))).all()} if ids else {}
    production = {
        asset_id: await asset_production_service.read_production(session, project, asset_id)
        for asset_id in links
    }
    result = {}
    versions = list((await session.scalars(select(AssetVersion).where(AssetVersion.asset_id.in_(ids)).order_by(AssetVersion.version.desc()))).all()) if ids else []
    for node in nodes:
        asset = assets.get(node.data.get("production_asset_id"))
        if not asset or asset.asset_type != node.node_type:
            continue
        own_versions = [v for v in versions if v.asset_id == asset.id]
        profile = asset.attributes.get("canvas_profile") or {"revision": 0,
            "views": [{"media_id": v.media_file_id, "label": f"形象版本 {v.version}"} for v in own_versions[:12]],
            "primary_media_id": next((v.media_file_id for v in own_versions if v.is_final), own_versions[0].media_file_id if own_versions else None),
            "voice_media_id": None}
        shared = production.get(asset.id)
        adopted = next((item for item in (shared or {}).get("adoptions", []) if item.get("key") == "default"), None)
        adopted = adopted or next(iter((shared or {}).get("adoptions", [])), None)
        if adopted:
            profile = {
                **profile,
                "primary_media_id": adopted["media_file_id"],
                "adopted_version_id": adopted["version_id"],
                "adoption_key": adopted["key"],
            }
        profile = {**profile, "token": entity_token(asset, links.get(asset.id))}
        if shared:
            profile.update({
                "production_revision": shared["revision"],
                "prompt_anchor": shared["prompt_anchor"],
                "asset_profile": shared["profile"],
                "sources": shared["sources"],
            })
        node_jobs = {
            item.get("media_id"): item.get("job_id")
            for item in (node.data or {}).get("media_versions", [])
            if item.get("media_id")
        }
        result[node.node_key] = {"title": asset.name, "content": asset.description or "",
            "production_asset_id": asset.id, "production_profile": profile,
            "production_readonly": asset.project_id != project.id,
            "media_id": profile.get("primary_media_id"),
            "pending_media_id": (node.data or {}).get("pending_media_id"),
            "media_versions": [{"media_id": v.media_file_id, "job_id": v.source_job_id or node_jobs.get(v.media_file_id)} for v in own_versions]
                or (node.data or {}).get("media_versions")
                or [{"media_id": v["media_id"], "job_id": None} for v in profile.get("views", [])]}
    return result


async def guard_revision(session, project, revision):
    changed = await session.execute(update(CanvasDocument).where(
        CanvasDocument.project_id == project.id, CanvasDocument.revision == revision,
    ).values(revision=CanvasDocument.revision + 1))
    if changed.rowcount != 1:
        raise ConflictError("画布已更新或尚未保存，请刷新后重试")


async def editable_node(session, project, node_key):
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    if node.node_type not in PRODUCTION_ENTITY_KINDS or node.data.get("projected"):
        raise ConflictError("请选择手工创建的共享资产节点")
    return node


async def scope_impact(session, project, node_key):
    node = await editable_node(session, project, node_key)
    asset = await owned_asset(session, project, node.data.get("production_asset_id"), node.node_type)
    link = await asset_production_service.link_for(session, project.id, asset.id)
    usages = list((await session.scalars(select(AssetUsage).where(
        AssetUsage.project_id == project.id,
        AssetUsage.asset_id == asset.id,
    ))).all())
    scene_ids = {usage.scene_id for usage in usages if usage.scene_id is not None}
    scenes = list((await session.scalars(select(Scene).where(Scene.id.in_(scene_ids)))).all()) if scene_ids else []
    episodes = {
        episode.id: episode
        for episode in (await session.scalars(select(Episode).where(Episode.project_id == project.id))).all()
    }
    segments = list((await session.scalars(
        select(VideoSegment)
        .join(EpisodeProduction, EpisodeProduction.active_plan_id == VideoSegment.plan_id)
        .where(
            EpisodeProduction.episode_id == VideoSegment.episode_id,
            VideoSegment.episode_id.in_(episodes),
        )
        .order_by(VideoSegment.episode_id, VideoSegment.order)
    )).all()) if episodes else []
    referenced_segments = [
        segment
        for segment in segments
        if any(
            item.get("asset_id") == asset.id
            for item in (segment.refs or {}).get("asset_bindings", [])
            if isinstance(item, dict)
        )
    ]
    override_keys = {
        (item.get("target_type"), item.get("target_id"))
        for item in (link.production_data or {}).get("scope_overrides", [])
        if isinstance(item, dict)
    }
    overrides = {
        (item.get("target_type"), item.get("target_id")): item
        for item in (link.production_data or {}).get("scope_overrides", [])
        if isinstance(item, dict)
    }
    targets = [
        {
            "target_type": "scene",
            "target_id": scene.id,
            "label": f"第 {episodes[scene.episode_id].number} 集 · 场景 {scene.order} · {scene.name}",
            "episode_id": scene.episode_id,
            "has_override": ("scene", scene.id) in override_keys,
            "override": overrides.get(("scene", scene.id)),
        }
        for scene in sorted(scenes, key=lambda item: (episodes[item.episode_id].number, item.order))
        if scene.episode_id in episodes
    ]
    targets.extend({
        "target_type": "segment",
        "target_id": segment.id,
        "label": f"第 {episodes[segment.episode_id].number} 集 · 片段 {segment.order:02d} · {segment.title or '未命名'}",
        "episode_id": segment.episode_id,
        "has_override": ("segment", segment.id) in override_keys,
        "override": overrides.get(("segment", segment.id)),
    } for segment in referenced_segments if segment.episode_id in episodes)
    canvas_cards = (await session.scalars(
        select(CanvasNode).where(CanvasNode.canvas_id == node.canvas_id)
    )).all()
    canvas_card_count = sum(
        1 for item in canvas_cards if (item.data or {}).get("production_asset_id") == asset.id
    )
    affected_episode_ids = {
        *(usage.episode_id for usage in usages if usage.episode_id is not None),
        *(segment.episode_id for segment in referenced_segments),
    }
    return {
        "asset_id": asset.id,
        "canvas_card_count": canvas_card_count,
        "usage_count": len(usages),
        "affected_episode_count": len(affected_episode_ids),
        "affected_segment_count": len(referenced_segments),
        "local_targets": targets,
    }


async def edit_entity(session, project, node_key, payload):
    await guard_revision(session, project, payload.expected_revision)
    node = await editable_node(session, project, node_key)
    asset = await owned_asset(session, project, node.data.get("production_asset_id"), node.node_type)
    link = await asset_production_service.link_for(session, project.id, asset.id)
    if payload.update_scope == "series" and asset.project_id != project.id:
        raise ConflictError("复用的全局或其他项目资产请在资产中心编辑，避免影响其他项目")
    if payload.update_scope == "series":
        # A second locked card must not be modified indirectly through shared identity.
        for linked in (await session.scalars(select(CanvasNode).where(CanvasNode.canvas_id == node.canvas_id))).all():
            if linked.data.get("production_asset_id") == asset.id:
                await canvas_service.assert_node_unlocked(session, project, linked)
    previous = asset.attributes.get("canvas_profile", {})
    if previous.get("revision", 0) != payload.expected_entity_revision or entity_token(asset, link) != payload.expected_entity_token:
        raise ConflictError("共享资产版本已变化，请重新打开编辑器")
    views = [v.model_dump() for v in payload.views]
    ids = [v["media_id"] for v in views]
    if len(set(ids)) != len(ids) or (payload.primary_media_id is not None and payload.primary_media_id not in ids):
        raise ConflictError("视图不能重复，主视图必须来自已绑定的形象/视图")
    if node.node_type != "character" and payload.voice_media_id is not None:
        raise ConflictError("仅角色支持独立音色参考")
    if payload.speech_preset:
        if node.node_type != "character":
            raise ConflictError("仅角色可关联配音预设")
        from app.providers.speech import speech_parameters
        from app.services.canvas_generation_service import validate_model
        model = await validate_model(session, payload.speech_preset.provider_model_id, "audio", {})
        from app.providers.protocols import effective_protocol
        from app.models import Provider
        provider = await session.get(Provider, model.provider_id)
        speech_parameters(model, {"voice": payload.speech_preset.voice}, "验证", protocol=effective_protocol(provider, model))
    primary_kind = "audio" if node.node_type == "voice" else "image"
    for media_id, kind in [(i, primary_kind) for i in ids] + ([(payload.voice_media_id, "audio")] if payload.voice_media_id else []):
        media = await session.get(MediaFile, media_id)
        if not media or not await same_team(session, media.owner_id, project.owner_id) or media.kind != kind:
            raise NotFoundError("引用素材不存在、类型不符或无权访问")
    name = payload.name.strip()
    if not name:
        raise ConflictError("名称不能为空")
    if payload.update_scope == "local":
        impact = await scope_impact(session, project, node_key)
        target = payload.local_target
        if target is None or not any(
            item["target_type"] == target.target_type and item["target_id"] == target.target_id
            for item in impact["local_targets"]
        ):
            raise ConflictError("局部修改目标未引用该资产或已不在当前制作计划中")
        if (
            name != asset.name
            or views != list(previous.get("views") or [])
            or payload.primary_media_id != previous.get("primary_media_id")
            or payload.voice_media_id != previous.get("voice_media_id")
            or (payload.speech_preset.model_dump() if payload.speech_preset else None) != previous.get("speech_preset")
        ):
            raise ConflictError("局部修改只能调整描述、提示词和状态资料，名称与素材属于全剧共享设定")
        shared = await asset_production_service.read_production(session, project, asset.id)
        local_profile = {
            key: value
            for key, value in (payload.profile.model_dump(mode="json") if payload.profile else {}).items()
            if shared["profile"].get(key) != value
        }
        await asset_production_service.update_production(session, project, asset.id, AssetProductionPatch(
            expected_revision=payload.expected_production_revision,
            request_id=payload.request_id,
            scope_override=AssetScopeOverride(
                target_type=target.target_type,
                target_id=target.target_id,
                description=payload.description,
                prompt_anchor=payload.prompt_anchor,
                profile=local_profile,
            ),
        ))
        await session.flush()
        return await canvas_service.get_snapshot(session, project)
    if payload.profile is not None or payload.prompt_anchor is not None:
        await asset_production_service.update_production(session, project, asset.id, AssetProductionPatch(
            expected_revision=payload.expected_production_revision,
            request_id=payload.request_id,
            **({"profile": payload.profile} if payload.profile is not None else {}),
            **({"prompt_anchor": payload.prompt_anchor} if payload.prompt_anchor is not None else {}),
        ))
    profile = {"revision": payload.expected_entity_revision + 1, "views": views,
        "primary_media_id": payload.primary_media_id, "voice_media_id": payload.voice_media_id,
        "speech_preset": payload.speech_preset.model_dump() if payload.speech_preset else None}
    asset.name, asset.description = name, payload.description
    asset.attributes = {**asset.attributes, "canvas_profile": profile}
    # Keep every image in the asset version history, even if later unbound from a card.
    versions = list((await session.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id))).all())
    next_version = max((v.version for v in versions), default=0)
    created_versions = []
    for view in views:
        if any(v.media_file_id == view["media_id"] for v in versions):
            continue
        next_version += 1
        created = AssetVersion(
            asset_id=asset.id, media_file_id=view["media_id"], version=next_version,
            prompt=view["label"], parameters={"source": "canvas-binding"},
            view_label=view["label"], view_type="base", review_status="candidate",
            tags=["画布"], is_final=False,
        )
        session.add(created)
        created_versions.append(created)
    for version in [*versions, *created_versions]:
        version.is_final = version.media_file_id == payload.primary_media_id
        if version.is_final:
            version.review_status = "approved"
    node.data = {**(node.data or {}), "media_id": payload.primary_media_id, "pending_media_id": None}
    await session.flush()
    return await canvas_service.get_snapshot(session, project)


async def bind_entity(session, project, node_key, payload):
    await guard_revision(session, project, payload.expected_revision)
    node = await editable_node(session, project, node_key)
    asset = await owned_asset(session, project, payload.asset_id, node.node_type)
    link = await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == asset.id))
    if not link:
        session.add(ProjectAssetLink(project_id=project.id, asset_id=asset.id, local_slug="reuse-" + uuid4().hex))
    node.data = {**node.data, "production_asset_id": asset.id}
    await session.flush()
    return await canvas_service.get_snapshot(session, project)


async def apply_plan(session, project, source, proposed):
    """All commands share the same transaction; callers commit only after success."""
    from app.schemas.canvas import CanvasSave
    from app.schemas.canvas_production import ProductionEdit, ProductionPlan
    plan = ProductionPlan.model_validate(proposed)
    snapshot = await canvas_service.get_snapshot(session, project)
    if snapshot["revision"] != source.get("revision"):
        raise ConflictError("提案已过期，请基于最新画布重新生成")
    from app.models import ProviderModel
    for model_id, expected in source.get("models", {}).items():
        model = await session.get(ProviderModel, int(model_id))
        from app.providers.protocols import model_confirmation_token
        if not model or await model_confirmation_token(session, model) != expected:
            raise ConflictError("模型参数、能力或计价已变化，请重新生成并确认提案")
    for command in plan.operations:
        nodes = {n["id"]: n for n in snapshot["nodes"]}
        node = nodes.get(command.node_id)
        if command.operation == "create":
            if node:
                raise ConflictError("提案的新节点 ID 已存在")
            snapshot["nodes"].append({"id": command.node_id, "type": command.kind,
                "x": 120 + len(nodes) % 3 * 470, "y": 120 + len(nodes) // 3 * 440, "width": 430,
                "data": {"title": command.title.strip() or "未命名", "content": command.content}})
            if source.get("request_id", "").startswith("workflow-"):
                # New nodes have not been measured by a browser yet. Reserve a real
                # card-sized box so a subsequent layout/group step cannot overlap rows.
                snapshot["nodes"][-1].update(width=360 if command.kind == "director" else 400,
                    height=360 if command.kind == "text" else 300 if command.kind == "director" else 600)
        else:
            if not node:
                raise NotFoundError("提案目标节点不存在")
            persisted = await canvas_service.get_canvas_node(session, project.id, command.node_id)
            await canvas_service.assert_node_unlocked(session, project, persisted)
            if command.operation == "director_update":
                from app.schemas.canvas_director import DirectorSave
                from app.services import canvas_director_service
                if command.kind != "director" or command.director_state is None or command.director_revision is None:
                    raise ConflictError("导演台提案必须包含完整结构化工程与源版本")
                await canvas_director_service.save(session, project, command.node_id, DirectorSave(
                    request_id=uuid4().hex, expected_revision=command.director_revision, state=command.director_state), respect_object_locks=True)
                snapshot = await canvas_service.get_snapshot(session, project)
                continue
            if command.operation == "generate":
                from app.services.canvas_generation_service import submit_media
                if command.kind not in {"image", "video", "audio"} or node["type"] != command.kind:
                    raise ConflictError("生成操作必须指向对应媒体节点")
                generated_job = await submit_media(session, project, node_key=command.node_id, task_type=command.kind,
                    provider_model_id=command.provider_model_id, prompt=command.content,
                    parameters={**command.parameters, "canvas_agent_thread_id": source.get("agent_thread_id")},
                    request_id=source.get("request_id"))
                effective = generated_job.payload.get("parameters", {})
                persisted.data = {**persisted.data, "provider_model_id": command.provider_model_id,
                    "content": command.content, "references": effective.get("references", []),
                    "aspect_ratio": effective.get("aspect_ratio"), "resolution": effective.get("resolution"),
                    "duration": str(effective["duration"]) if effective.get("duration") else None,
                    "voice": effective.get("voice")}
                await guard_revision(session, project, snapshot["revision"])
                snapshot = await canvas_service.get_snapshot(session, project)
                continue
            elif command.operation == "bind_media":
                if node["type"] in PRODUCTION_ENTITY_KINDS:
                    profile = node["data"].get("production_profile", {})
                    if source.get("entities", {}).get(command.node_id) not in (None, profile.get("token")):
                        raise ConflictError("实体已变更，请重新生成提案")
                    media = await session.get(MediaFile, command.media_id)
                    if not media or not await same_team(session, media.owner_id, project.owner_id) or media.kind not in {"image", "audio"}:
                        raise NotFoundError("绑定素材不存在或类型不符")
                    views = list(profile.get("views", []))
                    expected_kind = "audio" if node["type"] == "voice" else "image"
                    if media.kind == expected_kind and not any(v["media_id"] == media.id for v in views):
                        views.append({"media_id": media.id, "label": "生成素材"})
                    snapshot = await edit_entity(session, project, command.node_id, ProductionEdit(
                        expected_revision=snapshot["revision"], expected_entity_revision=profile.get("revision", 0), expected_entity_token=profile["token"],
                        update_scope="series",
                        name=node["data"]["title"], description=node["data"].get("content", ""), views=views,
                        primary_media_id=profile.get("primary_media_id") or (media.id if media.kind == expected_kind else None),
                        voice_media_id=media.id if media.kind == "audio" else profile.get("voice_media_id"), speech_preset=profile.get("speech_preset")))
                else:
                    snapshot = await canvas_service.attach_media(session, project, command.node_id, command.media_id)
                continue
            elif command.operation == "connect":
                if command.target_id not in nodes or command.target_id == command.node_id:
                    raise ConflictError("连线端点不存在或重复")
                target = await canvas_service.get_canvas_node(session, project.id, command.target_id)
                await canvas_service.assert_node_unlocked(session, project, target)
                if not any(e["source"] == command.node_id and e["target"] == command.target_id for e in snapshot["edges"]):
                    snapshot["edges"].append({"id": uuid4().hex, "source": command.node_id, "target": command.target_id, "data": {}})
            elif node["type"] != command.kind or node["data"].get("projected"):
                raise ConflictError("提案目标类型不符或属于只读业务投影")
            elif command.kind in {"text", "image", "video", "audio", "director"}:
                node["data"] = {**node["data"], "title": command.title.strip() or node["data"].get("title", "文本"), "content": command.content}
            else:
                profile = node["data"].get("production_profile", {})
                expected = source.get("entities", {}).get(command.node_id)
                if expected is not None and expected != profile.get("token"):
                    raise ConflictError("提案引用的实体版本已变化")
                snapshot = await edit_entity(session, project, command.node_id, ProductionEdit(
                    expected_revision=snapshot["revision"], expected_entity_revision=profile.get("revision", 0), expected_entity_token=profile["token"],
                    update_scope="series",
                    name=command.title.strip() or node["data"]["title"], description=command.content,
                    views=profile.get("views", []), primary_media_id=profile.get("primary_media_id"), voice_media_id=profile.get("voice_media_id"), speech_preset=profile.get("speech_preset")))
                continue
        snapshot = await canvas_service.save_snapshot(session, project, CanvasSave(
            expected_revision=snapshot["revision"], viewport=snapshot["viewport"], nodes=snapshot["nodes"], edges=snapshot["edges"]))
    return snapshot
