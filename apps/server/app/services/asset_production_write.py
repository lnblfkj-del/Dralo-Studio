"""Production overlay writes and idempotent patch handling."""

from app.services.asset_production_shared import *
from app.services.asset_production_core import fingerprint, link_for, read_production

async def update_production(
    session: AsyncSession, project: Project, asset_id: int, patch: AssetProductionPatch
) -> dict[str, Any]:
    project_id = project.id
    request_hash = fingerprint(
        {"asset_id": asset_id, **patch.model_dump(mode="json", exclude_unset=True)}
    )
    try:
        return await _update_production(session, project, asset_id, patch, request_hash)
    except (ConflictError, IntegrityError):
        # Another transaction may have committed after our receipt/revision reads.
        # Roll back ALL losing writes before reading the winner's durable receipt.
        # In particular, request IDs are shared across assets within a project.
        await session.rollback()
        receipt = await session.scalar(
            select(AssetProductionReceipt).where(
                AssetProductionReceipt.project_id == project_id,
                AssetProductionReceipt.request_id == patch.request_id,
            )
        )
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise ConflictError("request_id已用于不同操作") from None
            return receipt.response
        # Do not turn unrelated integrity errors into a successful replay.
        raise

async def _update_production(
    session: AsyncSession,
    project: Project,
    asset_id: int,
    patch: AssetProductionPatch,
    request_hash: str,
) -> dict[str, Any]:
    link = await link_for(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    receipt = await session.scalar(
        select(AssetProductionReceipt).where(
            AssetProductionReceipt.project_id == project.id,
            AssetProductionReceipt.request_id == patch.request_id,
        )
    )
    if receipt:
        if receipt.request_hash != request_hash:
            raise ConflictError("request_id已用于不同操作")
        return receipt.response
    if link.production_revision != patch.expected_revision:
        raise ConflictError("资产制作资料已变化，请刷新后合并修改")
    changed_fields = patch.model_fields_set - {"request_id", "expected_revision"}
    if link.production_archived and changed_fields != {"archived"}:
        raise ConflictError("资产已归档，请先恢复后再修改制作资料或素材")
    data = dict(link.production_data or {})
    if patch.prompt_anchor is not None:
        data["prompt_anchor"] = patch.prompt_anchor.strip()
    if patch.profile is not None:
        profile = patch.profile
        if profile.character_asset_id is not None:
            character = await asset_service.get_asset(
                session, project.id, profile.character_asset_id
            )
            if character.asset_type != "character" or character.id == asset.id:
                raise ValidationError("造型/声音必须关联当前项目中独立的角色资产")
        if asset.asset_type != "voice" and profile.audio_usage != "unclassified":
            raise ValidationError("只有声音资产能设置声音用途")
        # A patch only replaces explicitly supplied profile fields; omitted fields survive.
        current = (await read_production(session, project, asset_id))["profile"]
        data["profile"] = {**current, **profile.model_dump(mode="json", exclude_unset=True)}
    if patch.sources is not None:
        for source in patch.sources:
            episode = None
            if source.episode_id:
                episode = await session.get(Episode, source.episode_id)
                if episode is None or episode.project_id != project.id:
                    raise ValidationError("资料来源分集不属于当前项目")
                if (
                    source.script_revision is not None
                    and source.script_revision > episode.script_revision
                ):
                    raise ValidationError("资料不能引用未来剧本版本")
            if source.scene_id:
                scene = await session.get(Scene, source.scene_id)
                if scene is None or scene.episode_id != source.episode_id:
                    raise ValidationError("资料来源场景不属于所选分集")
            if source.shot_id:
                shot = await session.get(Shot, source.shot_id)
                if shot is None or shot.scene_id != source.scene_id:
                    raise ValidationError("资料来源分镜不属于所选场景")
            if source.segment_id:
                segment = await session.get(VideoSegment, source.segment_id)
                if segment is None or segment.episode_id != source.episode_id:
                    raise ValidationError("资料来源片段不属于所选分集")
            if source.canvas_node_key:
                canvas_id = await session.scalar(
                    select(CanvasDocument.id).where(CanvasDocument.project_id == project.id)
                )
                node = await session.scalar(
                    select(CanvasNode.id).where(
                        CanvasNode.canvas_id == canvas_id,
                        CanvasNode.node_key == source.canvas_node_key,
                    )
                )
                if node is None:
                    raise ValidationError("资料来源画布节点不属于当前项目")
        data["sources"] = [source.model_dump(mode="json") for source in patch.sources]
    if patch.adoptions is not None:
        choices = []
        for choice in patch.adoptions:
            version = await session.get(AssetVersion, choice.version_id)
            if (
                version is None
                or version.asset_id != asset_id
                or version.review_status == "archived"
            ):
                raise ValidationError("采用版本不存在、不属于该资产或已归档")
            if version.view_type == asset_service.LAYOUT_SHEET_VIEW_TYPE:
                raise ValidationError("排版预览需先拆分为独立素材，不能直接采用")
            media = await session.get(MediaFile, version.media_file_id)
            expected = (
                "audio"
                if asset.asset_type == "voice"
                else "video"
                if asset.asset_type == "video"
                else "image"
            )
            if (
                media is None
                or media.kind != expected
                or not await same_team(session, project.owner_id, media.owner_id)
            ):
                raise ValidationError("采用媒体类型错误或无权访问")
            choices.append(
                {
                    "key": choice.key,
                    "version_id": version.id,
                    "media_file_id": media.id,
                    "kind": media.kind,
                    "origin": "explicit",
                }
            )
        data["adoptions"] = choices
    if patch.scope_override is not None:
        override = patch.scope_override
        if override.target_type == "scene":
            target = await session.get(Scene, override.target_id)
            episode = await session.get(Episode, target.episode_id) if target else None
            if target is None or episode is None or episode.project_id != project.id:
                raise ValidationError("局部场景不属于当前项目")
        else:
            target = await session.get(VideoSegment, override.target_id)
            episode = await session.get(Episode, target.episode_id) if target else None
            if target is None or episode is None or episode.project_id != project.id:
                raise ValidationError("局部片段不属于当前项目")
        current_profile = (await read_production(session, project, asset_id))["profile"]
        AssetProfile.model_validate({**current_profile, **override.profile})
        overrides = list(data.get("scope_overrides") or [])
        key = (override.target_type, override.target_id)
        serialized = override.model_dump(mode="json")
        overrides = [
            item
            for item in overrides
            if (item.get("target_type"), item.get("target_id")) != key
        ]
        overrides.append(serialized)
        data["scope_overrides"] = overrides
    archived = link.production_archived if patch.archived is None else patch.archived
    result = await session.execute(
        update(ProjectAssetLink)
        .where(
            ProjectAssetLink.id == link.id,
            ProjectAssetLink.production_revision == patch.expected_revision,
        )
        .values(
            production_data=data,
            production_revision=patch.expected_revision + 1,
            production_archived=archived,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ConflictError("资产制作资料已被其他请求更新")
    await session.refresh(link)
    response = await read_production(session, project, asset_id)
    session.add(
        AssetProductionReceipt(
            project_id=project.id,
            asset_id=asset_id,
            request_id=patch.request_id,
            request_hash=request_hash,
            response=response,
        )
    )
    await session.flush()
    return response
