"""Load explicit administrator-owned capabilities; never guess from model names."""

import json

from pydantic import ValidationError as SchemaError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Provider, ProviderModel
from app.providers.protocols import effective_protocol, validate_model_protocol
from app.schemas.episode_planning import VideoCapability
from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint

CONFIG_KEY = "episode_planning_capability"


async def load_capability(session: AsyncSession, model_id: int) -> VideoCapability:
    model = await session.get(ProviderModel, model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if model is None or provider is None:
        raise NotFoundError("视频模型或渠道不存在")
    if not model.enabled or not provider.enabled or model.model_type != "video":
        raise ConflictError("请选择已启用的视频模型")
    validate_model_protocol(provider, model)
    raw = (model.default_params or {}).get(CONFIG_KEY)
    if not isinstance(raw, dict):
        raise ConflictError("该视频模型尚未配置新规划能力，请先核验具体模式和规格")
    try:
        capability = VideoCapability.model_validate_json(json.dumps(raw))
    except (SchemaError, TypeError, ValueError) as exc:
        raise ConflictError("视频模型规划能力配置不完整，不能推测默认规格") from exc
    if (
        capability.provider_model_id != model.id
        or capability.model_id != model.model_id
        or capability.protocol != effective_protocol(provider, model)
        or capability.endpoint_fingerprint != video_prompt_endpoint_fingerprint(provider, model)
    ):
        raise ConflictError("视频模型路由已变化，请重新核验规划能力")
    if capability.verification != "channel_verified":
        raise ConflictError("该渠道模式尚未实测验证，不能提交正式规划任务")
    return capability
