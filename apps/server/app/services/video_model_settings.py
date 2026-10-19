"""Translate ordinary model settings into the existing planning contract.

The mode matrix is authoritative; flat catalog fields are its editable summary.
Saving declarations never creates channel-test evidence or makes provider calls.
"""

import hashlib
import itertools
import json
from decimal import Decimal
from types import SimpleNamespace

from app.core.errors import ConflictError
from app.providers.protocols import effective_protocol
from app.schemas.episode_planning import DurationRule, VideoCapability
from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint

CONFIG_KEY = "episode_planning_capability"
FIELDS = {"durations", "resolutions", "aspect_ratios", "max_reference_images",
          "supported_video_input_modes", "max_shots_per_segment", "supports_audio", "supports_dialogue"}
MODE_FLAGS = {"text_to_video": ("text",), "image_to_video": ("single_image",),
              "first_frame": ("first_frame",), "first_last_frame": ("first_last_frame",),
              "multi_reference": ("multi_reference",), "reference_images": ("single_image", "multi_reference")}


def route_identity(provider, model):
    return {"provider_model_id": model.id, "model_id": model.model_id,
            "protocol": effective_protocol(provider, model),
            "endpoint_fingerprint": video_prompt_endpoint_fingerprint(provider, model)}


def parse(raw):
    try:
        result = VideoCapability.model_validate_json(json.dumps(raw, allow_nan=False))
        if len(result.modes) > 64:
            raise ValueError("Too many modes")
        return result
    except (TypeError, ValueError) as exc:
        raise ConflictError("视频设置不完整，请核对生成方式、时长、比例和分辨率") from exc


def duration_values(rule):
    if rule.kind == "discrete":
        return rule.values_ms
    count = (rule.maximum_ms - rule.minimum_ms) // rule.step_ms + 1
    if count > 128:
        raise ConflictError("支持时长超过 128 档，请缩小范围或增大间隔")
    return tuple(range(rule.minimum_ms, rule.maximum_ms + 1, rule.step_ms))


def summary(capability):
    modes = capability.modes
    return {
        "durations": sorted({ms / 1000 for m in modes for ms in duration_values(m.durations)}),
        "resolutions": sorted({m.resolution for m in modes}),
        "aspect_ratios": sorted({m.aspect_ratio for m in modes}),
        "supported_video_input_modes": sorted({m.input_mode for m in modes}),
        "max_reference_images": max((r.maximum for m in modes for r in m.reference_limits if r.role == "image"), default=0),
        "max_shots_per_segment": max(m.max_shots for m in modes),
        "supports_audio": any(m.native_audio for m in modes),
        "supports_dialogue": any(m.native_dialogue for m in modes),
    }


def store(model, capability):
    from app.services.video_configuration import from_legacy, persist, projected_params

    params = projected_params(model)
    params.update(model.default_params or {})
    values = summary(capability)
    inputs = values["supported_video_input_modes"]
    flags = set(model.capabilities or []) - MODE_FLAGS.keys()
    for flag, modes in MODE_FLAGS.items():
        if flag != "reference_images" and any(mode in inputs for mode in modes):
            flags.add(flag)
    model.capabilities = sorted(flags)
    persist(model, from_legacy(model, capability, params=params))


def display_settings(model):
    from app.services.video_configuration import projected_params

    params = projected_params(model)
    view = SimpleNamespace(default_params=params, capabilities=list(model.capabilities or []))
    raw = params.get(CONFIG_KEY)
    if raw is not None:
        cap = parse(raw)
        values = summary(cap)
        flags = set(view.capabilities) - MODE_FLAGS.keys()
        flags.update(flag for flag, modes in MODE_FLAGS.items()
                     if flag != "reference_images" and any(m in values["supported_video_input_modes"] for m in modes))
        view.capabilities = sorted(flags)
        view.default_params = {**params, **values}
    return view


def input_modes(params, flags):
    if "supported_video_input_modes" in params:
        return params["supported_video_input_modes"]
    return sorted({mode for flag, modes in MODE_FLAGS.items() if flag in flags for mode in modes})


def missing_fields(params, flags):
    for field in ("resolutions", "aspect_ratios", "supported_video_input_modes"):
        if field in params and (not isinstance(params[field], list) or any(
            not isinstance(value, str) or not value.strip() for value in params[field]
        )):
            raise ConflictError("生成方式、比例和分辨率必须是有效的文本选项")
    missing = [label for key, label in (("durations", "支持时长"), ("resolutions", "分辨率"),
                                       ("aspect_ratios", "画幅比例")) if not params.get(key)]
    modes = input_modes(params, flags)
    if not modes:
        missing.append("生成方式")
    if any(m in {"single_image", "multi_reference"} for m in modes) and "max_reference_images" not in params:
        missing.append("参考图数量")
    return missing


def _duration_rule(values):
    try:
        milliseconds = [Decimal(str(value)) * 1000 for value in values]
        if any(not value.is_finite() or value != int(value) or value <= 0 for value in milliseconds):
            raise ValueError("Invalid milliseconds")
        if len(milliseconds) > 128:
            raise ValueError("Too many durations")
        return DurationRule(kind="discrete", values_ms=tuple(sorted({int(v) for v in milliseconds}))).model_dump(mode="json")
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ConflictError("支持时长须为正数，最多 128 档，精度不超过毫秒") from exc


def _references(mode, maximum):
    roles = {"text": [], "first_frame": [("first_frame", 1, 1)],
             "first_last_frame": [("first_frame", 1, 1), ("last_frame", 1, 1)],
             "single_image": [("image", 1, 1)], "multi_reference": [("image", 1, maximum)]}
    if mode not in roles:
        raise ConflictError(f"生成方式 {mode} 需要单独配置对应素材限制")
    if mode in {"single_image", "multi_reference"} and (type(maximum) is not int or not 1 <= maximum <= 32):
        raise ConflictError("图片参考模式需要填写 1 到 32 张参考图上限")
    limits = [{"role": role, "minimum": low, "maximum": high} for role, low, high in roles[mode]]
    return {"reference_limits": limits, "max_total_references": sum(r["maximum"] for r in limits)}


def mode_key(mode):
    identity = [mode[k] for k in ("input_mode", "aspect_ratio", "resolution")]
    return "video-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]


def build(provider, model, *, params=None):
    from app.services.video_configuration import projected_params

    params, flags = projected_params(model) if params is None else params, model.capabilities or []
    missing = missing_fields(params, flags)
    if missing:
        raise ConflictError("请在视频模型设置中补充：" + "、".join(missing))
    inputs = input_modes(params, flags)
    if len(inputs) * len(params["aspect_ratios"]) * len(params["resolutions"]) > 64:
        raise ConflictError("视频设置组合超过 64 项，请减少重复选项")
    duration = _duration_rule(params["durations"])
    modes = []
    for input_mode, ratio, resolution in itertools.product(inputs, params["aspect_ratios"], params["resolutions"]):
        mode = {"input_mode": input_mode, "aspect_ratio": ratio, "resolution": resolution,
                "durations": duration, "max_shots": params.get("max_shots_per_segment", 4 if "multi_shot" in flags else 1),
                "native_audio": params.get("supports_audio", "audio" in flags),
                "native_dialogue": params.get("supports_dialogue", "dialogue" in flags),
                "bgm_control": "prompt_preference", **_references(input_mode, params.get("max_reference_images", 0))}
        modes.append({"key": mode_key(mode), **mode})
    return parse({**route_identity(provider, model), "verification": "documented",
                  "evidence": ["administrator-model-settings"], "modes": modes})


def synchronize(provider, model, previous_flags=None, supplied_params=None):
    """Normalize old API input once; persist only the structured configuration."""
    if model.model_type != "video":
        return
    from app.services.video_configuration import from_legacy, persist, projected_params

    initial_config = getattr(model, "video_config", None)
    view = SimpleNamespace(
        id=model.id, model_id=model.model_id, model_type=model.model_type,
        api_protocol=model.api_protocol, api_base_url=model.api_base_url,
        capabilities=list(model.capabilities or []),
        video_config=initial_config,
        default_params={**projected_params(model), **(model.default_params or {})},
    )
    _synchronize_fields(provider, view, previous_flags, supplied_params)
    if getattr(view, "video_config", None) is initial_config:
        persist(view, from_legacy(view, provenance="legacy-incomplete"))
    from app.services.video_configuration import read, validate_defaults

    validate_defaults(read(view))
    model.video_config = view.video_config
    model.default_params = {}
    model.capabilities = view.capabilities


def _synchronize_fields(provider, model, previous_flags=None, supplied_params=None):
    """Apply only explicitly changed summary fields; preserve all other mode limits."""
    if model.model_type != "video":
        return
    params, flags = model.default_params or {}, model.capabilities or []
    raw = params.get(CONFIG_KEY)
    if raw is None:
        if previous_flags is not None and (set(previous_flags) & MODE_FLAGS.keys()) != (set(flags) & MODE_FLAGS.keys()):
            params = {**params, "supported_video_input_modes": input_modes({}, flags)}
            model.default_params = params
        if not missing_fields(params, flags):
            store(model, build(provider, model, params=params))
        return
    old = parse(raw)
    supplied = supplied_params or {}
    values = summary(old)
    changed = {key for key in FIELDS if key in supplied and supplied[key] != values[key]}
    old_flags, new_flags = set(previous_flags or []) & MODE_FLAGS.keys(), set(flags) & MODE_FLAGS.keys()
    values.update({key: supplied[key] for key in changed})
    if old_flags != new_flags:
        values["supported_video_input_modes"] = input_modes({}, flags)
        changed.add("supported_video_input_modes")
    route_changed = any(getattr(old, key) != value for key, value in route_identity(provider, model).items())
    explicit_settings = all(key in supplied and supplied[key] for key in ("durations", "resolutions", "aspect_ratios"))
    if not changed and not (route_changed and explicit_settings):
        store(model, old)
        return
    missing = missing_fields(values, flags)
    if missing:
        raise ConflictError("请补充视频设置：" + "、".join(missing))
    # Changing a route is not evidence that the new route supports the old modes.
    if route_changed and not explicit_settings:
        raise ConflictError("模型接口已变化，请重新核对该接口支持的视频设置")
    selected = set(values["supported_video_input_modes"])
    added_inputs = selected - {m.input_mode for m in old.modes}
    revised = {}
    for original in old.modes:
        if original.input_mode not in selected:
            continue
        def choices(field, attr):
            current = getattr(original, attr)
            if field not in changed:
                return [current]
            previous = {getattr(m, attr) for m in old.modes}
            return ([current] if current in values[field] else []) + [v for v in values[field] if v not in previous]

        for ratio, resolution in itertools.product(
            choices("aspect_ratios", "aspect_ratio"), choices("resolutions", "resolution"),
        ):
            mode = original.model_dump(mode="json")
            mode.update(aspect_ratio=ratio, resolution=resolution)
            if "durations" in changed:
                mode["durations"] = _duration_rule(values["durations"])
            for field, target in (("max_shots_per_segment", "max_shots"), ("supports_audio", "native_audio"),
                                  ("supports_dialogue", "native_dialogue")):
                if field in changed:
                    mode[target] = values[field]
            if "max_reference_images" in changed and any(r.role == "image" for r in original.reference_limits):
                if any(r.role != "image" for r in original.reference_limits):
                    raise ConflictError("该方式同时使用图片和其他素材，请分别修改各类素材上限")
                maximum = values["max_reference_images"]
                limit = next(r for r in original.reference_limits if r.role == "image")
                if type(maximum) is not int or not limit.minimum <= maximum <= 32:
                    raise ConflictError("参考图上限不能小于该生成方式需要的图片数量")
                maximum = min(maximum, 1) if original.input_mode == "single_image" else maximum
                mode["reference_limits"] = [{"role": "image", "minimum": limit.minimum, "maximum": maximum}]
                mode["max_total_references"] = min(maximum, original.max_total_references + maximum - limit.maximum)
            identity = (original.input_mode, ratio, resolution)
            if identity in revised:
                if {k: v for k, v in mode.items() if k != "key"} != {k: v for k, v in revised[identity].items() if k != "key"}:
                    raise ConflictError("不同生成方式或分辨率的限制不同，请分别修改，不能合并为同一组设置")
                continue
            if (ratio, resolution) != (original.aspect_ratio, original.resolution):
                mode["key"] = mode_key(mode)
            revised[identity] = mode
    if added_inputs:
        signatures = {(m.durations, m.max_shots, m.native_audio, m.native_dialogue, m.bgm_control) for m in old.modes}
        if len(signatures) > 1:
            raise ConflictError("不同模式的时长或声音限制不同，请单独填写新增生成方式的设置")
        candidate = SimpleNamespace(id=model.id, model_id=model.model_id, api_protocol=model.api_protocol,
                                    api_base_url=model.api_base_url, capabilities=flags,
                                    video_config=getattr(model, "video_config", None),
                                    default_params={**values, "supported_video_input_modes": sorted(added_inputs)})
        for mode in build(provider, candidate, params=candidate.default_params).modes:
            revised[(mode.input_mode, mode.aspect_ratio, mode.resolution)] = mode.model_dump(mode="json")
    next_cap = parse({**route_identity(provider, model), "verification": "documented",
                      "evidence": ["administrator-model-settings"], "modes": list(revised.values())})
    if next_cap.modes == old.modes and not route_changed:
        store(model, old)
    else:
        store(model, next_cap)
