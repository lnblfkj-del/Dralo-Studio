"""Canonical video-model capability contract used by planning and submission."""

from typing import Any


def audio_contract(provider, model):
    """Capabilities belong to a verified route, not a model name or audio flag."""
    from app.providers.protocols import effective_protocol, is_toapis_model
    from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint
    defaults = model.default_params or {}
    evidence = defaults.get("native_audio_capability") or {}
    endpoint = video_prompt_endpoint_fingerprint(provider, model)
    result = {"verified": False, "output": "unverified", "dialogue": False,
              "audio_reference": False, "bgm_control": "prompt_preference",
              "parameter": None, "endpoint_fingerprint": endpoint,
              "warning": "当前渠道原生声音尚未验证；配乐为提示词偏好，不能保证无配乐。无原生声音时需后期配音。"}
    if not isinstance(evidence, dict) or evidence.get("endpoint_fingerprint") != endpoint or not evidence.get("evidence"):
        return result
    if evidence.get("status") != "verified" or evidence.get("output") not in {"always_on", "optional", "unsupported"}:
        return result
    protocol = effective_protocol(provider, model)
    fields = {"ark_video_t2v": "generate_audio", "ark_video_images": "generate_audio",
              "dashscope_video_t2v": "audio", "dashscope_video_i2v": "audio",
              "kling_video_t2v": "generate_audio", "kling_video_i2v": "generate_audio"}
    field = fields.get(protocol)
    if is_toapis_model(provider, model):
        field = "generate_audio" if model.model_id == "seedance-2-5" else "audio" if model.model_id in {
            "kling-v3-omni", "kling-v2-6", "wan2.6", "wan2.6-flash"} else None
    if protocol == "fake_video":
        field = "generate_audio"
    if evidence["output"] == "optional" and (not field or evidence.get("parameter") != field):
        return result
    result.update(verified=True, output=evidence["output"],
                  dialogue=evidence.get("dialogue") is True and evidence["output"] != "unsupported",
                  parameter=field if evidence["output"] == "optional" else None,
                  evidence=evidence["evidence"],
                  warning="该渠道不支持原生声音，请使用后期配音。" if evidence["output"] == "unsupported"
                  else "背景音乐仅通过提示词约束；实际声音需试听核对。")
    if result["output"] != "unsupported" and not result["dialogue"]:
        result["warning"] = "本渠道尚未验证原生对白；不能以有音轨代替配音验收。" + result["warning"]
    return result

from app.core.errors import ConflictError, ValidationError
from app.models import ProviderModel


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def normalize(model: ProviderModel) -> dict[str, Any]:
    defaults = model.default_params or {}
    raw_durations = defaults.get("durations")
    if not isinstance(raw_durations, list):
        raw_durations = []
    durations = sorted({float(item) for item in raw_durations if type(item) in (int, float) and 0 < float(item) <= 30})
    if not durations:
        raise ConflictError("视频模型尚未配置支持时长，不能用于片段规划")
    capabilities = set(model.capabilities or [])
    configured_max = defaults.get("max_shots_per_segment")
    multi_shot = "multi_shot" in capabilities or type(configured_max) is int and configured_max > 1
    max_shots = int(configured_max) if type(configured_max) is int and configured_max > 0 else (4 if multi_shot else 1)
    supports_references = bool(capabilities & {"reference_images", "multi_reference"})
    inferred_ref_limit = 7 if model.model_id in {"grok-video-1.5", "kling-v3-omni"} and "multi_reference" in capabilities else 4
    max_refs = defaults.get("max_reference_images", inferred_ref_limit if supports_references else 0)
    if type(max_refs) is not int or max_refs < 0:
        max_refs = 0
    return {
        "provider_model_id": model.id,
        "model_id": model.model_id,
        "name": model.name,
        "durations": durations,
        "aspect_ratios": _strings(defaults.get("aspect_ratios")),
        "resolutions": [item.lower() for item in _strings(defaults.get("resolutions"))],
        "multi_shot": multi_shot,
        "max_shots_per_segment": min(max_shots, 20),
        "max_reference_images": min(max_refs, 32),
        "supports_first_frame": bool(defaults.get(
            "supports_first_frame", bool(capabilities & {"first_frame", "image_to_video"})
        )),
        "supports_last_frame": bool(defaults.get("supports_last_frame", "last_frame" in capabilities)),
        "supports_reference_images": bool(supports_references and max_refs > 0),
        "supported_video_input_modes": _strings(defaults.get("supported_video_input_modes")) or [
            mode for mode, enabled in (
                ("text", True),
                ("first_frame", bool(defaults.get("supports_first_frame", capabilities & {"first_frame", "image_to_video"}))),
                ("single_image", supports_references and max_refs > 0),
                ("multi_reference", supports_references and max_refs > 1),
                ("first_last_frame", bool(defaults.get("supports_last_frame", "last_frame" in capabilities))),
            ) if enabled
        ],
        "supports_audio": bool(defaults.get("supports_audio", "audio" in capabilities)),
        "supports_dialogue": bool(defaults.get("supports_dialogue", "dialogue" in capabilities)),
        "pricing_snapshot": dict(model.pricing or {}),
    }


def canonical_parameters(contract: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    result = dict(parameters)
    if (contract.get("model_id") == "kling-v3-omni"
            and isinstance(result.get("resolution"), str) and result["resolution"] in {"std", "pro"}):
        result["resolution"] = {"std": "720p", "pro": "1080p"}[result["resolution"]]
    return result


def validate_parameters(contract: dict[str, Any], parameters: dict[str, Any]) -> None:
    parameters = canonical_parameters(contract, parameters)
    for field, choices in (("aspect_ratio", "aspect_ratios"), ("resolution", "resolutions")):
        value = parameters.get(field)
        allowed = contract.get(choices) or []
        if field == "resolution" and contract.get("model_id") == "kling-v3-omni":
            allowed = [canonical_parameters(contract, {"resolution": item})["resolution"] for item in allowed]
        if value not in (None, "default", "模型默认") and (not allowed or str(value) not in allowed):
            raise ValidationError(f"视频模型不支持参数 {field}={value}")
    preferred = parameters.get("preferred_duration")
    if preferred is not None and float(preferred) not in contract["durations"]:
        raise ValidationError(f"视频模型不支持 {preferred} 秒生成时长")
    requested_max = parameters.get("max_shots_per_segment")
    if requested_max is not None and (
        type(requested_max) is not int or requested_max < 1 or requested_max > contract["max_shots_per_segment"]
    ):
        raise ValidationError("片段分镜上限超出模型能力")
    for field, supported in (
        ("first_frame_media_id", "supports_first_frame"),
        ("last_frame_media_id", "supports_last_frame"),
    ):
        if parameters.get(field) is not None and not contract[supported]:
            raise ValidationError(f"视频模型不支持参数 {field}")
    if bool(parameters.get("generate_audio") or parameters.get("audio")) and not contract["supports_audio"]:
        raise ValidationError("视频模型未声明原生声音生成能力，不能静默发送声音参数")


def choose_duration(contract: dict[str, Any], timeline: float) -> float:
    for duration in contract["durations"]:
        if duration + 0.05 >= timeline:
            return float(duration)
    raise ValidationError(f"片段时间轴 {timeline:g} 秒超过模型最大生成时长")


def validate_segment(
    contract: dict[str, Any], *, generation_duration: float, shot_count: int,
    timeline_duration: float, reference_count: int | None = None,
) -> None:
    # Script asset associations are not a video request. Only the submission
    # preflight knows the actual images, including frames and project style.
    if float(generation_duration) not in contract["durations"]:
        raise ValidationError(f"模型不支持 {generation_duration:g} 秒生成时长")
    if shot_count > contract["max_shots_per_segment"]:
        raise ValidationError("片段包含的分镜数超过模型能力")
    if timeline_duration > generation_duration + 0.05:
        raise ValidationError("片段时间轴时长超过模型生成时长")
    if reference_count is not None and reference_count > contract["max_reference_images"]:
        raise ValidationError(
            f"实际参考图 {reference_count} 张，超过模型上限 {contract['max_reference_images']} 张；"
            "请调整视频参考图或先合成镜头首帧，已保存的片段脚本不受影响"
        )
