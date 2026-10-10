"""Read-only input selection and validation before a paid planning request."""

from sqlalchemy import and_, exists, or_, select

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import Asset, AssetVersion, MediaFile, ProjectAssetLink, ProjectMediaLink
from app.services.episode_planning_contract import fingerprint
from app.services.team_access import owner_scope


def input_fingerprint(sources, assets, capability, mode_key, background_music, script_revision):
    from app.services.episode_planning_workflow import _digest

    return _digest(
        {
            "sources": fingerprint(sources),
            "assets": assets,
            "capability": fingerprint(capability),
            "mode": mode_key,
            "background_music": background_music,
            "script_revision": script_revision,
        }
    )


async def source_context(session, episode):
    from app.services.episode_planning_workflow import _source_context

    sources, _ = await _source_context(session, episode)
    return {
        "script_revision": episode.script_revision,
        "source_fingerprint": fingerprint(sources),
        "sources": [unit.model_dump(mode="json") for unit in sources.units],
    }


async def preflight(
    session,
    episode,
    *,
    video_model_id,
    mode_key,
    background_music,
    expected_script_revision,
    expected_source_fingerprint,
    reference_bindings,
):
    from app.services.episode_planning_capability import load_capability
    from app.services.episode_planning_references import resolve_bindings
    from app.services.episode_planning_workflow import _digest, _source_context
    from app.services.episode_preparation_gate import require_preparation

    if not settings.episode_planning_epoch.strip():
        raise ConflictError("新规划执行命名空间尚未初始化")
    await require_preparation(session, episode)
    if episode.script_revision != expected_script_revision:
        raise ConflictError("正文版本已变化，请重新读取素材绑定范围")
    sources, assets = await _source_context(session, episode)
    if fingerprint(sources) != expected_source_fingerprint:
        raise ConflictError("正文或角色解析结果已变化，请重新核对素材绑定范围")
    capability = await load_capability(session, video_model_id)
    mode = next((item for item in capability.modes if item.key == mode_key), None)
    if mode is None:
        raise ConflictError("所选视频模式已变化，请重新选择")
    if background_music and mode.bgm_control == "unsupported":
        raise ConflictError("该模式不支持所选配乐设置")
    references = await resolve_bindings(session, episode, sources, mode, reference_bindings)
    assets = _digest({"assets": assets, "references": references})
    return {
        "fingerprint": input_fingerprint(
            sources, assets, capability, mode_key, background_music, episode.script_revision
        ),
        "reference_count": len(references),
        "model_called": False,
        "video_submission_ready": False,
    }


async def reference_options(session, episode, *, kind, keyword, offset, limit):
    active_asset = exists(
        select(ProjectAssetLink.id).where(
            ProjectAssetLink.project_id == episode.project_id,
            ProjectAssetLink.asset_id == AssetVersion.asset_id,
            ProjectAssetLink.production_archived.is_(False),
        )
    )
    any_project_asset = exists(
        select(AssetVersion.id)
        .join(
            ProjectAssetLink,
            ProjectAssetLink.asset_id == AssetVersion.asset_id,
        )
        .where(
            ProjectAssetLink.project_id == episode.project_id,
            AssetVersion.media_file_id == MediaFile.id,
        )
    ).correlate(MediaFile)
    direct_link = exists(
        select(ProjectMediaLink.id).where(
            ProjectMediaLink.project_id == episode.project_id,
            ProjectMediaLink.media_file_id == MediaFile.id,
        )
    )
    statement = (
        select(MediaFile, AssetVersion, Asset)
        .select_from(MediaFile)
        .outerjoin(
            AssetVersion,
            and_(AssetVersion.media_file_id == MediaFile.id, active_asset),
        )
        .outerjoin(Asset, Asset.id == AssetVersion.asset_id)
        .where(
            owner_scope(MediaFile.owner_id, episode.owner_id),
            MediaFile.purpose == "creative",
            MediaFile.kind == kind,
            MediaFile.hash.is_not(None),
            or_(
                and_(AssetVersion.id.is_not(None), owner_scope(Asset.owner_id, episode.owner_id)),
                and_(
                    ~any_project_asset, or_(MediaFile.project_id == episode.project_id, direct_link)
                ),
            ),
        )
    )
    if keyword.strip():
        term = keyword.strip()
        statement = statement.where(
            or_(
                Asset.name.contains(term, autoescape=True),
                MediaFile.original_name.contains(term, autoescape=True),
            )
        )
    rows = (
        await session.execute(
            statement.order_by(
                MediaFile.id.desc(),
                AssetVersion.id.desc(),
            )
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {
        "items": [
            {
                "media_id": media.id,
                "asset_version_id": version.id if version else None,
                "kind": media.kind,
                "label": f"{asset.name} · V{version.version} · {version.view_label}"
                if version
                else (media.original_name or f"素材 #{media.id}"),
                "width": media.width,
                "height": media.height,
                "duration": media.duration,
            }
            for media, version, asset in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }
