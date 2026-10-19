"""Reusable voice identity, explicitly scoped to its model and account."""

# ruff: noqa: RUF001

from app.core.errors import ConflictError
from app.models import Provider
from app.providers.protocols import effective_protocol
from app.providers.speech import speech_parameters
from app.schemas.production_contract import SpeechBinding
from app.services.audio_verification_service import check_fingerprint


def require_scope(binding, provider, model, voice):
    if not binding or binding.get("provider_model_id") != model.id or binding.get("voice") != voice:
        return
    recorded = binding.get("account_fingerprint")
    if recorded and recorded != check_fingerprint(provider, model):
        raise ConflictError("绑定音色的渠道账户或接法已变化，请重新选择并保存音色")


async def validate_binding(session, raw):
    from app.services.canvas_generation_service import validate_model

    binding = SpeechBinding.model_validate(raw)
    model = await validate_model(session, binding.provider_model_id, "audio", {"voice": binding.voice})
    provider = await session.get(Provider, model.provider_id)
    require_scope(binding.model_dump(), provider, model, binding.voice)
    speech_parameters(model, {"voice": binding.voice}, "测", protocol=effective_protocol(provider, model), provider=provider)
    return {"provider_model_id": model.id, "voice": binding.voice,
            "account_fingerprint": check_fingerprint(provider, model)}


async def require_node_scopes(session, project, node, provider, model, voice):
    from sqlalchemy import select

    from app.models import Asset, CanvasEdge, CanvasNode, ProjectAssetLink
    from app.services.asset_audio_profile import effective_profile

    sources = (await session.scalars(select(CanvasNode).join(CanvasEdge,
        (CanvasEdge.canvas_id == CanvasNode.canvas_id) & (CanvasEdge.source_key == CanvasNode.node_key)).where(
        CanvasEdge.canvas_id == node.canvas_id, CanvasEdge.target_key == node.node_key,
        CanvasNode.node_type.in_(["character", "voice"])))).all()
    for source in sources:
        asset = await session.get(Asset, (source.data or {}).get("production_asset_id"))
        if asset is None:
            continue
        link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == asset.id))
        if link:
            require_scope(effective_profile(asset, link.production_data).get("speech_preset"), provider, model, voice)
