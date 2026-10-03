"""Freeze episode-scoped assets and authored input for one recoverable text job."""

from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Asset, AssetUsage, Project, ProjectAssetLink, Scene
from app.services.asset_binding_service import resolve_asset_binding
from app.services.asset_production_catalog import _source_episode_numbers
from app.services.episode_empty_scenes import empty_placeholders


def _remove_ambiguous_catalog_aliases(catalog):
    """Keep aliases usable as identity keys only when they have one owner."""
    canonical = {
        str(item.get("asset_name") or "").strip().casefold(): item["asset_id"]
        for item in catalog
        if str(item.get("asset_name") or "").strip()
    }
    owners = {}
    for item in catalog:
        for value in item.get("aliases") or []:
            key = str(value).strip().casefold()
            if key:
                owners.setdefault(key, set()).add(item["asset_id"])
    for item in catalog:
        asset_id = item["asset_id"]
        filtered = []
        for value in item.get("aliases") or []:
            text = str(value).strip()
            key = text.casefold()
            if not text or len(owners.get(key, set())) != 1:
                continue
            canonical_owner = canonical.get(key)
            if canonical_owner is not None and canonical_owner != asset_id:
                continue
            filtered.append(text)
        item["aliases"] = list(dict.fromkeys(filtered))
    return catalog


async def enrich_input(session, episode, production, snapshot):
    if not snapshot["shots"]:
        await empty_placeholders(session, episode)
    project = await session.get(Project, episode.project_id)
    explicit = set((production.settings or {}).get("asset_ids") or [])
    used = set((await session.scalars(select(AssetUsage.asset_id).where(
        AssetUsage.project_id == episode.project_id, AssetUsage.episode_id == episode.id,
    ))).all())
    catalog = []
    rows = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == episode.project_id,
            ProjectAssetLink.production_archived.is_(False)).order_by(Asset.id))).all()
    for asset, link in rows:
        data = link.production_data or {}
        if asset.id not in explicit | used and episode.number not in _source_episode_numbers(data):
            continue
        profile = data.get("profile") or {}
        role = "audio_reference" if asset.asset_type == "voice" else "reference_image"
        raw = {"asset_id": asset.id, "asset_name": asset.name,
               "asset_type": asset.asset_type, "role": role, "resolved": False}
        try:
            raw.update(await resolve_asset_binding(session, project, asset_id=asset.id, role=role))
        except (ConflictError, NotFoundError, ValidationError) as exc:
            raw["unresolved_reason"] = exc.message
        catalog.append({**raw, "description": asset.description, "prompt_anchor": asset.prompt_anchor,
                        "profile": profile, "aliases": profile.get("aliases") or []})
    _remove_ambiguous_catalog_aliases(catalog)
    scenes = list((await session.scalars(select(Scene).where(
        Scene.episode_id == episode.id,
    ).order_by(Scene.order, Scene.id))).all())
    return {**snapshot, "asset_catalog": catalog,
            "scene_snapshot": [{"id": s.id, "name": s.name, "order": s.order,
                                "location": s.location, "time_of_day": s.time_of_day,
                                "description": s.description} for s in scenes],
            "source_lines": [{"line": i, "text": text.strip()} for i, text in
                             enumerate((episode.script or "").splitlines(), 1) if text.strip()]}


def prompt_extension(snapshot):
    return (
        "\n本次为正文到片段脚本的自动草稿任务。无需用户预先拆解镜头。"
        "只返回一个 JSON，保留 shots、segments、continuity_issues，新增 scenes 和 shot_sources。"
        "有输入 shots 时 scenes 返回 []；锁定镜头原样保留。非锁定镜头可重拆，新增镜头用大于现有最大 shot_id 的局部编号，"
        "不得复用其他旧镜头 ID；重拆时每个正文行都须有来源映射。"
        "没有输入 shots 时从正文规划镜头：scenes 填局部 scene_id（从1开始）、name、location、"
        "time_of_day、description；shots.shot_id 使用从1开始的局部编号，不能冒充数据库ID。"
        "shot_sources 为每个镜头填写 shot_id、scene_id、source_lines（正文行号）、asset_ids、"
        "unresolved_names（未能唯一匹配的资产名）。必须按正文顺序覆盖每个非空原文行，"
        "对话和 BGM/音效/环境声原句须保留在对应镜头 dialogue/audio_note 中，不压缩或删除台词。"
        "片段数由剧情、场景变化和视频模型能力决定；target_duration 是估时，整集允许上下 5 秒浮动，不靠空镜凑时长。"
        "资产只能选择 asset_catalog 内的ID，按角色身份、别名、造型和场景匹配；"
        "同名或多造型不能唯一判断时填写 unresolved_names，不猜测。资料可用于写脚本，"
        "未就绪的声音、图片不能声称已经生成。只需正文存在的配乐，不凭空加音频文件。"
        "已有镜头也须按其内容匹配资产，不能仅依赖已有 asset_bindings。"
    )
