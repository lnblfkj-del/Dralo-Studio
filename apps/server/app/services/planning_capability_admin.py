"""Maintain explicit specs without inferring capabilities or calling providers."""

import hashlib
import json

from pydantic import ValidationError as SchemaError
from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError, ValidationError
from app.models import Job, Provider, ProviderModel
from app.providers.protocols import effective_protocol, validate_model_protocol
from app.schemas.episode_planning import VideoCapability
from app.services import provider_catalog_service as catalog
from app.services.episode_planning_capability import CONFIG_KEY, load_capability
from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint


def route_identity(provider, model):
    return {
        "provider_model_id": model.id, "model_id": model.model_id,
        "protocol": effective_protocol(provider, model),
        "endpoint_fingerprint": video_prompt_endpoint_fingerprint(provider, model),
    }


def config_fingerprint(provider, model):
    value = {
        "route": route_identity(provider, model), "default_params": model.default_params,
        "capabilities": model.capabilities, "pricing": model.pricing,
        "certifications": model.video_prompt_certifications,
        "model_type": model.model_type, "enabled": [provider.enabled, model.enabled],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parse_capability(raw):
    try:
        if len(json.dumps(raw)) > 100_000:
            raise ValueError("Configuration too large")
        result = VideoCapability.model_validate_json(json.dumps(raw))
        if len(result.modes) > 64 or any(len(mode.durations.values_ms) > 128 for mode in result.modes):
            raise ValueError("Too many modes or durations")
        return result
    except (SchemaError, TypeError, ValueError) as exc:
        raise ValidationError("规划规格不完整或无效，请核对模式、时长、参考数量和验证证据") from exc


async def read_state(session, provider, model):
    if model.model_type != "video":
        raise ConflictError("规划规格只适用于视频模型")
    raw = (model.default_params or {}).get(CONFIG_KEY)
    capability = None
    try:
        if raw is not None:
            capability = parse_capability(raw).model_dump(mode="json")
        await load_capability(session, model.id)
        ready, reason = True, None
    except (ConflictError, ValidationError) as exc:
        ready, reason = False, exc.message
    return {
        "route": route_identity(provider, model),
        "config_fingerprint": config_fingerprint(provider, model),
        "capability": capability, "planning_ready": ready, "blocking_reason": reason,
        "model_called": False,
    }


async def lock_catalog(session, provider_id, model_id):
    # Use the same lock order on SQLite and PostgreSQL. No-op writes acquire
    # row/write locks without changing catalog timestamps or unrelated fields.
    await session.execute(update(Provider).where(Provider.id == provider_id).values(
        name=Provider.name, updated_at=Provider.updated_at))
    provider = await catalog.get_provider(session, provider_id)
    await session.execute(update(ProviderModel).where(
        ProviderModel.id == model_id, ProviderModel.provider_id == provider_id,
    ).values(name=ProviderModel.name, updated_at=ProviderModel.updated_at))
    model = await catalog.get_model(session, provider_id, model_id)
    await session.refresh(provider)
    await session.refresh(model)
    return provider, model


async def assert_no_active_planning(session, provider, model):
    await catalog._assert_no_active_jobs(session, provider.id, model.id)
    child = aliased(Job)
    terminal = {"succeeded", "failed", "cancelled"}
    active_child = exists(select(child.id).where(child.parent_job_id == Job.id, child.status.not_in(terminal)))
    active = await session.scalar(select(Job.id).where(
        Job.target_type == "episode_content_planning",
        Job.payload["request_spec"]["video_model_id"].as_integer() == model.id,
        or_(Job.status.not_in(terminal), active_child),
    ).limit(1))
    if active:
        raise ConflictError("该视频模型仍有整集规划任务执行中，请完成或取消后再修改验证配置")


async def save_capability(session, provider_id, model_id, payload):
    provider, model = await lock_catalog(session, provider_id, model_id)
    if model.model_type != "video":
        raise ConflictError("规划规格只适用于视频模型")
    if config_fingerprint(provider, model) != payload.expected_config_fingerprint:
        raise ConflictError("模型配置已变化，请重新读取后核对规格；未覆盖已有配置")
    capability = parse_capability(payload.capability)
    identity = route_identity(provider, model)
    if any(getattr(capability, key) != value for key, value in identity.items()):
        raise ConflictError("规划规格与当前模型路由不一致，请重新读取；不会自动换绑验证证据")
    if capability.verification == "channel_verified" and not payload.acknowledge_channel_verification:
        raise ValidationError("标记渠道实测前，请明确确认本模型当前路由的全部模式已经实测")
    validate_model_protocol(provider, model, configuration=True)
    await assert_no_active_planning(session, provider, model)
    model.default_params = {**(model.default_params or {}), CONFIG_KEY: capability.model_dump(mode="json")}
    await session.flush()
    return await read_state(session, provider, model)
