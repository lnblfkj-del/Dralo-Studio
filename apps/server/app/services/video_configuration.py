"""Single persisted video configuration with read-only legacy projections."""

import hashlib
import json
from copy import deepcopy

from app.core.errors import ConflictError
from app.schemas.episode_planning import VideoCapability
from app.schemas.video_configuration import VideoConfig, VideoConfigInput

CONFIG_KEY = "episode_planning_capability"
SPEC_KEYS = {
    CONFIG_KEY, "durations", "resolutions", "aspect_ratios", "max_reference_images",
    "supported_video_input_modes", "supports_first_frame", "supports_last_frame",
    "supports_audio", "supports_dialogue", "supports_negative_prompt",
}
POLICY_KEYS = {"max_shots_per_segment"}
EVIDENCE_KEYS = {"native_audio_capability", "_audio_verification"}
DEFAULT_KEYS = {"duration", "resolution", "aspect_ratio", "generate_audio", "audio", "seed"}
FORBIDDEN = {"api_key", "authorization", "headers", "base_url", "model", "messages", "stream",
             "video_prompt_certifications", "prompt_timeline"}


def fingerprint(model):
    raw = getattr(model, "video_config", None)
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read(model):
    raw = getattr(model, "video_config", None)
    if raw is None:
        return None
    try:
        return VideoConfig.model_validate_json(json.dumps(raw, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ConflictError("视频配置无效，请重新保存视频设置") from exc


def capability(model):
    config = read(model)
    if config is None or config.specification is None:
        return None
    return VideoCapability.model_validate_json(json.dumps({
        **config.route, **config.declaration,
        "modes": config.specification.model_dump(mode="json")["modes"],
    }))


def projected_params(model):
    config = read(model)
    if config is None:
        return deepcopy(model.default_params or {})
    params = {**config.transport_params, **config.generation_defaults, **config.pending_settings,
              **config.planning_policy, **config.evidence}
    cap = capability(model)
    if cap is not None:
        from app.services.video_model_settings import summary

        params.update(summary(cap))
        if config.specification.supports_negative_prompt is not None:
            params["supports_negative_prompt"] = config.specification.supports_negative_prompt
        inputs = params["supported_video_input_modes"]
        params.update(supports_first_frame=any(m in inputs for m in ("first_frame", "first_last_frame")),
                      supports_last_frame="first_last_frame" in inputs,
                      **{CONFIG_KEY: cap.model_dump(mode="json")})
    return params


def request_defaults(model):
    config = read(model)
    if config is None:
        return {key: value for key, value in (model.default_params or {}).items()
                if key not in SPEC_KEYS | POLICY_KEYS | EVIDENCE_KEYS}
    return {**config.transport_params, **config.generation_defaults}


def from_legacy(model, cap=None, *, provenance="model-settings", params=None):
    params = dict(params if params is not None else model.default_params or {})
    route, declaration = {}, {}
    if cap is not None:
        raw = cap.model_dump(mode="json")
        route = {key: raw[key] for key in ("provider_model_id", "model_id", "protocol", "endpoint_fingerprint")}
        declaration = {key: raw[key] for key in ("verification", "evidence")}
    return VideoConfig.model_validate_json(json.dumps({
        "schema_version": "video-model.v1",
        "integration": (getattr(model, "video_config", None) or {}).get("integration"),
        "specification": {"modes": cap.model_dump(mode="json")["modes"],
                          "supports_negative_prompt": params.get("supports_negative_prompt")} if cap else None,
        "route": route, "declaration": declaration,
        "generation_defaults": {k: v for k, v in params.items() if k in DEFAULT_KEYS},
        "planning_policy": {k: v for k, v in params.items() if k in POLICY_KEYS},
        "transport_params": {k: v for k, v in params.items()
                             if k not in SPEC_KEYS | POLICY_KEYS | EVIDENCE_KEYS | DEFAULT_KEYS},
        "evidence": {k: v for k, v in params.items() if k in EVIDENCE_KEYS},
        "pending_settings": {k: v for k, v in params.items() if k in SPEC_KEYS - {CONFIG_KEY}}
                            if cap is None else {},
        "provenance": provenance,
    }, allow_nan=False))


def persist(model, config):
    model.video_config = config.model_dump(mode="json")
    model.default_params = {}


def validate_defaults(config):
    if config.integration is not None:
        from app.providers.configured_video import sources

        mapped = sources(config.integration.request)
        if not {"model", "prompt"} <= mapped:
            raise ConflictError("自定义接法必须映射模型和提示词")
        for key in config.generation_defaults.keys() | config.transport_params.keys():
            if key not in mapped and f"param.{key}" not in mapped:
                raise ConflictError(f"自定义接法没有映射默认参数 {key}，不能静默丢弃")
    for values in (config.generation_defaults, config.transport_params, config.planning_policy):
        json.dumps(values, allow_nan=False)
        if FORBIDDEN.intersection(values) or (SPEC_KEYS | EVIDENCE_KEYS).intersection(values):
            raise ConflictError("接口参数不能覆盖连接、支持规格或测试记录")
    if set(config.generation_defaults) - DEFAULT_KEYS:
        raise ConflictError("默认生成参数包含未登记字段，请移到接口附加参数")
    if set(config.planning_policy) - POLICY_KEYS:
        raise ConflictError("创作偏好包含未登记字段")
    if (DEFAULT_KEYS | POLICY_KEYS).intersection(config.transport_params):
        raise ConflictError("默认生成参数和创作偏好须分别设置，不能放入接口附加参数")
    limit = config.planning_policy.get("max_shots_per_segment")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 20):
        raise ConflictError("每段建议镜头数须为1到20的整数")
    defaults = config.generation_defaults
    duration = defaults.get("duration")
    if duration is not None and (type(duration) not in (int, float) or duration <= 0):
        raise ConflictError("默认生成时长必须为正数")
    for field in ("audio", "generate_audio"):
        if field in defaults and type(defaults[field]) is not bool:
            raise ConflictError("默认声音选项必须为布尔值")
    if config.specification and not any(
        all(defaults.get(field) is None or defaults[field] == getattr(mode, field)
            for field in ("resolution", "aspect_ratio"))
        and (duration is None or float(duration * 1000).is_integer()
             and mode.durations.allows(int(duration * 1000)))
        and (not (defaults.get("audio") or defaults.get("generate_audio")) or mode.native_audio)
        for mode in config.specification.modes
    ):
        raise ConflictError("默认时长、分辨率、画幅或声音不属于同一组支持规格，请调整默认设置")


def save_input(provider, model, raw):
    from app.services.video_model_settings import parse, route_identity, store

    incoming = VideoConfigInput.model_validate(raw)
    previous = capability(model)
    from app.providers.protocols import effective_protocol

    if (effective_protocol(provider, model) == "custom_video") != (incoming.integration is not None):
        raise ConflictError("自定义视频接法必须附带接法配置；现成协议不要附带自定义接法")
    initial = read(model) or from_legacy(model)
    persist(model, initial.model_copy(update={"integration": incoming.integration}))
    identity = route_identity(provider, model)
    cap = parse({**identity, "verification": "documented", "evidence": ["administrator-model-settings"],
                 "modes": incoming.specification.model_dump(mode="json")["modes"]})
    if previous and previous.modes == cap.modes and all(getattr(previous, k) == v for k, v in identity.items()):
        cap = previous
    store(model, cap)
    saved = read(model).model_copy(update={
        "specification": incoming.specification,
        "generation_defaults": incoming.generation_defaults,
        "planning_policy": incoming.planning_policy,
        "transport_params": incoming.transport_params,
    })
    validate_defaults(saved)
    persist(model, saved)
