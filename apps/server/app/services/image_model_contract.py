"""Image input limits and provider-safe generation parameters."""

from app.core.errors import ConflictError

METADATA_KEYS = {"aspect_ratios", "resolutions", "durations", "max_reference_images",
                 "supports_negative_prompt", "reference_limits", "costume_mode", "costume_views", "costume_direction"}


def reference_limit(model):
    if "reference_images" not in (model.capabilities or []):
        return 0
    limit = (getattr(model, "default_params", None) or {}).get("max_reference_images", 4)
    if type(limit) is not int or not 0 <= limit <= 32:
        raise ConflictError("模型参考图数量配置无效")
    return limit


def validate_image_inputs(model, parameters, references):
    allowed = (getattr(model, "default_params", None) or {}).get("aspect_ratios") or []
    ratio = parameters.get("aspect_ratio")
    if allowed and ratio not in (None, "", "default", "project") and ratio not in allowed:
        raise ConflictError(f"图片模型不支持画幅 {ratio}")
    count = len(set(references))
    limit = reference_limit(model)
    if count > limit:
        raise ConflictError(f"实际参考图 {count} 张，超过模型上限 {limit} 张（包含角色与风格参考）")


def compile_negative(prompt, negative_prompt, parameters):
    if negative_prompt and parameters.get("supports_negative_prompt") is not True:
        return prompt + "\n\n禁止内容与构图：" + negative_prompt.strip(), None
    return prompt, negative_prompt
