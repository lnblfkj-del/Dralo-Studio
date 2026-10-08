"""Garment-only assets and character-bound worn references are distinct inputs."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import ProjectAssetLink


async def resolve_mode(session, project_id, asset, parameters):
    if asset.asset_type != "costume":
        return None
    link = await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == asset.id,
    )) if project_id else None
    profile = ((link.production_data or {}).get("profile") or {}) if link else {}
    attrs = asset.attributes or {}
    mode = parameters.get("costume_mode") or profile.get("costume_mode") or attrs.get("costume_mode")
    if not mode:
        mode = "worn" if profile.get("character_asset_id") or attrs.get("linked_character_asset_ids") or attrs.get("linked_character_asset_id") else "garment_only"
    if mode not in {"garment_only", "worn"}:
        raise ConflictError("服装生成模式无效")
    return mode


def garment_prompt(prompt, direction="正面"):
    return (f"服装资料：\n{prompt.strip()}\n\n服装本体参考图：只展示同一套服装的{direction}，"
            "衣物从领口到下摆、袖口及配件完整入镜，材料、颜色、层次、裁剪和缝线清晰，四周保留留白。"
            "使用无可见人体的隐形支撑或服装平铺展示，浅灰中性背景、均匀影棚光。"
            "禁止人物、脸、头、皮肤、手、脚、人体、人台、模特、剧情场景、多宫格、文字与水印。"
            "资产资料中的人物名称仅说明服装归属，不是画面主体；不得因风格联想改变已确定的衣裤类型。")


def views(count):
    if count == "three":
        return [("front", "正面"), ("side", "侧面"), ("back", "背面")]
    if count == "four":
        return [("front", "正面"), ("left", "左侧面"), ("right", "右侧面"), ("back", "背面")]
    if count not in (None, "single"):
        raise ConflictError("服装视图模式无效")
    return [("front", "正面")]
