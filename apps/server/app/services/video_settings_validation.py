"""Keep catalog summaries from widening per-mode submission restrictions."""

from decimal import Decimal, InvalidOperation

from app.core.errors import ConflictError
from app.services.video_model_settings import CONFIG_KEY, parse, route_identity


def validate_settings(provider, model, parameters, *, input_mode, first, last, references):
    from app.services.video_configuration import projected_params

    raw = projected_params(model).get(CONFIG_KEY)
    if raw is None:
        if getattr(model, "video_config", None) is not None:
            from app.providers.protocols import effective_protocol
            from app.services.video_model_settings import missing_fields

            if effective_protocol(provider, model) != "fake_video":
                missing = missing_fields(projected_params(model), model.capabilities or [])
                raise ConflictError("视频模型支持规格尚未完整配置，请补充：" + "、".join(missing or ["分模式规格"]))
        return
    capability = parse(raw)
    if any(getattr(capability, key) != value for key, value in route_identity(provider, model).items()):
        raise ConflictError("视频模型接口已变化，请重新保存并核对视频设置")
    counts = {"first_frame": int(bool(first)), "last_frame": int(bool(last)), "image": len(references)}
    try:
        duration = parameters.get("duration", parameters.get("seconds"))
        milliseconds = Decimal(str(duration)) * 1000 if duration is not None else None
    except InvalidOperation as exc:
        raise ConflictError("视频时长无效，请选择支持的时长") from exc
    for mode in capability.modes:
        if mode.input_mode != input_mode and not (input_mode == "single_image" and mode.input_mode == "multi_reference"):
            continue
        if any(parameters.get(field) is not None and parameters[field] != getattr(mode, field)
               for field in ("aspect_ratio", "resolution")):
            continue
        if milliseconds is not None and (not milliseconds.is_finite() or milliseconds != int(milliseconds)
                                         or not mode.durations.allows(int(milliseconds))):
            continue
        limits = {limit.role: limit for limit in mode.reference_limits}
        if any(count and role not in limits for role, count in counts.items()):
            continue
        if any(not limit.minimum <= counts.get(role, 0) <= limit.maximum for role, limit in limits.items()):
            continue
        if sum(counts.values()) > mode.max_total_references:
            continue
        if (parameters.get("generate_audio") is True or parameters.get("audio") is True) and not mode.native_audio:
            continue
        return
    raise ConflictError("所选生成方式、时长、比例、分辨率或素材数量不符合该视频模型设置，请调整后再生成")
