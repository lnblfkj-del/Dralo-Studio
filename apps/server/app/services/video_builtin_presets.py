"""Only exact, fully specified model contracts already maintained in this repository."""

from itertools import product

from app.providers.video_contracts import VIDEO_PROTOCOL_TEMPLATES
from app.services.video_model_settings import _references, mode_key


def packages():
    result = {}
    for protocol, template in VIDEO_PROTOCOL_TEMPLATES.items():
        params = template["default_params"]
        # Generic protocol envelopes and model-name hints are not model capabilities.
        if not template.get("model_id_locked") or not all(params.get(field) for field in ("durations", "resolutions", "aspect_ratios")):
            continue
        modes = []
        for input_mode, ratio, resolution in product(template["input_modes"], params["aspect_ratios"], params["resolutions"]):
            mode = {"input_mode": input_mode, "aspect_ratio": ratio, "resolution": resolution,
                    "durations": {"kind": "discrete", "values_ms": [int(value * 1000) for value in params["durations"]]},
                    "max_shots": params.get("max_shots_per_segment", 1),
                    "native_audio": params.get("supports_audio", False), "native_dialogue": params.get("supports_dialogue", False),
                    "bgm_control": "unsupported", **_references(input_mode, params.get("max_reference_images", 0))}
            modes.append({"key": mode_key(mode), **mode})
        result[protocol] = {"model_ids": [template["model_id_hint"]],
            "package": {"name": template["name"], "protocol": protocol, "video_config": {
                "schema_version": "video-model.v1", "specification": {"modes": modes},
                "generation_defaults": {}, "planning_policy": {}, "transport_params": {}}}}
    return result
