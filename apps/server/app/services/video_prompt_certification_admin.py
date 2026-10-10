"""Route-bound evidence registration with no HTTP calls or task creation."""

import json

from app.core.errors import ConflictError, ValidationError
from app.services.planning_capability_admin import (
    assert_no_active_planning,
    config_fingerprint,
    lock_catalog,
    route_identity,
)
from app.services.video_prompt_compiler import (
    INPUT_MODES,
    NATIVE_MULTI_SHOT_FIELDS,
    resolve_model_prompt_profile,
    validate_video_prompt_certifications,
)
from app.services.video_prompt_timeline import TIMELINE_ROUTES


def read_state(provider, model):
    if model.model_type != "video":
        raise ConflictError("提示词认证只适用于视频模型")
    route = route_identity(provider, model)
    config = model.video_prompt_certifications or {}
    return {
        "route": route, "config_fingerprint": config_fingerprint(provider, model),
        "certifications": config,
        "certification_stale": bool(config and config.get("endpoint_fingerprint") != route["endpoint_fingerprint"]),
        "timeline_supported": (route["protocol"], route["model_id"]) in TIMELINE_ROUTES,
        "profiles": {mode: resolve_model_prompt_profile(provider, model, mode) for mode in sorted(INPUT_MODES)},
        "model_called": False,
    }


async def save_certifications(session, provider_id, model_id, payload):
    provider, model = await lock_catalog(session, provider_id, model_id)
    state = read_state(provider, model)
    if state["config_fingerprint"] != payload.expected_config_fingerprint:
        raise ConflictError("模型配置已变化，请重新读取后核对认证；未覆盖已有配置")
    try:
        if len(json.dumps(payload.certifications, allow_nan=False)) > 30_000:
            raise ValueError("Configuration too large")
        config = validate_video_prompt_certifications(payload.certifications)
    except (ValueError, TypeError) as exc:
        raise ValidationError(str(exc)) from exc
    if config:
        route = state["route"]
        if config["endpoint_fingerprint"] != route["endpoint_fingerprint"]:
            raise ConflictError("提示词认证与当前路由不一致；不会自动换绑旧证据")
        for mode, record in config["modes"].items():
            if record["status"] == "channel_verified" and not payload.acknowledge_channel_verification:
                raise ValidationError("登记渠道实测前，请确认本次保存的所有实测模式均有当前路由的验收证据")
            if record.get("prompt_timeline") and not state["timeline_supported"]:
                raise ValidationError("当前路由尚未实现提示词时间轴适配，不能登记多镜头时间轴认证")
            if record.get("native_multi_shot") or record.get("native_multi_shot_parameter"):
                parameter = NATIVE_MULTI_SHOT_FIELDS.get((route["protocol"], route["model_id"]))
                if not parameter or record.get("native_multi_shot_parameter") != parameter:
                    raise ValidationError("当前路由尚未实现对应原生多镜头提交字段")
            if record.get("production_enabled"):
                if not provider.enabled or not model.enabled:
                    raise ConflictError("渠道或模型已停用，不能启用生产提示词")
                if not state["profiles"][mode]["local_adapter_preflight"]["passed"]:
                    raise ValidationError("当前输入模式未通过本地适配器预检，不能启用生产提示词")
                if state["profiles"][mode]["recipe"].startswith("h3_"):
                    raise ValidationError("H3 官方提示词生产提交尚未开放，离线预览不能作为启用依据")
    await assert_no_active_planning(session, provider, model)
    model.video_prompt_certifications = config
    await session.flush()
    return read_state(provider, model)
