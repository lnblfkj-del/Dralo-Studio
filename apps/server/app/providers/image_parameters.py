"""Translate UI image ratios to the OpenAI image size contract."""

from app.core.errors import ConflictError
from app.services.image_model_contract import METADATA_KEYS


def openai_image_parameters(model, parameters):
    values = {key: value for key, value in parameters.items()
              if key not in METADATA_KEYS}
    if not model.startswith("gpt-image-"):
        return values
    ratio = values.pop("aspect_ratio", None)
    resolution = str(values.pop("resolution", "1k")).lower()
    if ratio in (None, "", "default"):
        return values
    sizes = {"16:9": (1536, 864), "9:16": (864, 1536), "1:1": (1024, 1024),
             "4:3": (1152, 864), "3:4": (864, 1152), "21:9": (1792, 768),
             "3:2": (1536, 1024), "2:3": (1024, 1536)}
    if ratio not in sizes:
        raise ConflictError(f"图片模型尚未适配画幅 {ratio}")
    width, height = sizes[ratio]
    if model.startswith("gpt-image-1") and ratio != "1:1":
        if ratio not in {"3:2", "2:3"}:
            raise ConflictError("该图片模型不支持精确项目画幅，请使用支持此画幅的模型")
    if resolution not in {"1k", "2k", "4k"}:
        raise ConflictError("图片分辨率必须为 1K、2K 或 4K")
    if model.startswith("gpt-image-1") and resolution != "1k":
        raise ConflictError("该图片模型不支持所选分辨率")
    if resolution != "1k":
        from math import gcd
        divisor = gcd(width, height)
        unit_w, unit_h = width // divisor, height // divisor
        max_edge = 2048 if resolution == "2k" else 3840
        max_pixels = 3686400 if resolution == "2k" else 8294400
        multiplier = min(max_edge // max(unit_w, unit_h),
                         int((max_pixels / (unit_w * unit_h)) ** 0.5))
        multiplier = multiplier // 16 * 16
        width, height = unit_w * multiplier, unit_h * multiplier
    values["size"] = f"{width}x{height}"
    return values
