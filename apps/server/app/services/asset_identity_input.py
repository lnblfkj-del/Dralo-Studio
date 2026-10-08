"""Freeze linked character identity and adopted images for costume generation."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Asset, Project, ProjectAssetLink
from app.services.asset_binding_service import resolve_asset_binding


async def costume_identity(session, project_id, asset_id, model, prompt, references, *, mode="worn"):
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.asset_type != "costume":
        return prompt, references
    if mode == "garment_only":
        return prompt, references
    project = await session.get(Project, project_id)
    link = await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == asset_id))
    attrs = asset.attributes or {}
    profile = ((link.production_data or {}).get("profile") or {}) if link else {}
    explicit = profile.get("character_asset_id")
    ids = [explicit] if explicit else attrs.get("linked_character_asset_ids") or [attrs.get("linked_character_asset_id")]
    ids = list(dict.fromkeys(value for value in ids if type(value) is int and value > 0))
    if not ids or project is None:
        raise ConflictError(f"造型 {asset.name} 尚未关联角色，请先关联角色并采用角色图片")
    if "reference_images" not in (model.capabilities or []):
        raise ConflictError("造型生成需要支持参考图片的模型，以保持角色一致性")
    identity, media_ids = [], []
    for character_id in ids:
        character = await session.get(Asset, character_id)
        if character is None or character.asset_type != "character":
            raise ConflictError("造型关联的角色无效")
        binding = await resolve_asset_binding(session, project, asset_id=character_id, expected_kind="image")
        media_ids.append(binding["media_file_id"])
        character_link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == character_id))
        character_data = character_link.production_data or {}
        anchor = character_data.get("prompt_anchor", character.prompt_anchor)
        identity.append(f"角色 {character.name}（参考图 {len(media_ids)}）：{anchor or character.description or ''}")
    refs = list(dict.fromkeys([*media_ids, *references]))
    from app.services.image_model_contract import validate_image_inputs
    validate_image_inputs(model, {}, refs)
    return ("角色身份约束：保持参考角色的物种、脸部、体型和标志特征；动物角色不得变成人类。"
            "只按后文修改服装、妆容和状态。\n" + "\n".join(identity) + "\n\n造型要求：\n" + prompt), refs
