"""Load explicit administrator-owned capabilities; never guess from model names."""

import json

from pydantic import ValidationError as SchemaError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Provider, ProviderModel
from app.providers.protocols import effective_protocol, validate_model_protocol
from app.schemas.episode_planning import VideoCapability
from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint
from app.services.video_model_settings import CONFIG_KEY, build


def same_specification(left: VideoCapability, right: VideoCapability) -> bool:
    excluded = {"verification", "evidence"}
    return left.model_dump(mode="json", exclude=excluded) == right.model_dump(mode="json", exclude=excluded)


def accepts_saved_shot_limits(current: VideoCapability, saved: VideoCapability) -> bool:
    """Increasing a shot ceiling does not invalidate an already requested plan."""
    saved_modes = {mode.key: mode for mode in saved.modes}
    if {mode.key for mode in current.modes} != saved_modes.keys():
        return False
    if any(mode.max_shots < saved_modes[mode.key].max_shots for mode in current.modes):
        return False
    comparable = current.model_copy(update={"modes": tuple(
        mode.model_copy(update={"max_shots": saved_modes[mode.key].max_shots})
        for mode in current.modes
    )})
    return same_specification(comparable, saved)


async def load_capability(session: AsyncSession, model_id: int) -> VideoCapability:
    model = await session.get(ProviderModel, model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if model is None or provider is None:
        raise NotFoundError("视频模型或渠道不存在")
    return capability_for(provider, model)


def capability_for(provider, model) -> VideoCapability:
    """Validate either the live model or an immutable execution view by the same rules."""
    if not model.enabled or not provider.enabled or model.model_type != "video":
        raise ConflictError("请选择已启用的视频模型")
    validate_model_protocol(provider, model)
    from app.services.video_configuration import projected_params

    raw = projected_params(model).get(CONFIG_KEY)
    if raw is None:
        return build(provider, model)
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
    if capability.verification == "mock_verified":
        raise ConflictError("该视频设置仅用于模拟测试，请填写实际模型支持的规格")
    return capability
