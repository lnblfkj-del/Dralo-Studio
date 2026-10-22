"""Image input limits and provider-safe generation parameters."""

from app.core.errors import ConflictError

METADATA_KEYS = {"aspect_ratios", "resolutions", "durations", "max_reference_images",
                 "supports_negative_prompt", "reference_limits", "costume_mode", "costume_views", "costume_direction"}


def reference_limit(model):
    if "reference_images" not in (model.capabilities or []):
        return 0
    limit = (getattr(model, "default_params", None) or {}).get("max_reference_images")
    if limit is None:
        raise ConflictError(f"图片模型 {getattr(model, 'model_id', '')} 尚未配置参考图上限，请在模型设置中按渠道能力填写")
    if type(limit) is not int or not 0 <= limit <= 32:
        raise ConflictError("模型参考图数量配置无效")
    return limit


def validate_image_inputs(model, parameters, references):
    allowed = (getattr(model, "default_params", None) or {}).get("aspect_ratios") or []
    ratio = parameters.get("aspect_ratio")
    if allowed and ratio not in (None, "", "default", "project") and ratio not in allowed:
        raise ConflictError(f"图片模型不支持画幅 {ratio}")
    count = len(set(references))
    limit = reference_limit(model) if count else 0
    if count > limit:
        raise ConflictError(f"图片模型 {getattr(model, 'model_id', '')}：实际参考图 {count} 张，超过图片模型上限 {limit} 张（包含角色与风格参考）")


async def project_image_references(session, project_id, owner_id, references):
    """Resolve the same deduplicated style input for preview and submission."""
    from app.models import MediaFile
    from app.services.style_generation_service import project_style_media

    refs = list(dict.fromkeys(references))
    style_id = await project_style_media(session, project_id, owner_id, "image")
    if style_id:
        media = await session.get(MediaFile, style_id)
        if media is None or media.kind != "image":
            raise ConflictError("项目风格参考图片不存在或不可用")
        if style_id not in refs:
            refs.append(style_id)
    return refs


def compile_negative(prompt, negative_prompt, parameters):
    if negative_prompt and parameters.get("supports_negative_prompt") is not True:
        return prompt + "\n\n禁止内容与构图：" + negative_prompt.strip(), None
    return prompt, negative_prompt
