"""Offline MiniMax H3 V2 parameter contract. No request is submitted here."""

from __future__ import annotations

from typing import Any

from app.core.errors import ConflictError

MODEL_LIMITS = {
    "MiniMax-H3": {"minimum_duration": 4, "resolutions": frozenset({"768P", "2K"})},
    "MiniMax-H3-Max": {"minimum_duration": 5, "resolutions": frozenset({"480P", "768P"})},
}
ASPECT_RATIOS = frozenset({"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"})
UNSUPPORTED_OPTIONS = frozenset({
    "fps", "seed", "width", "height", "n", "audio", "generate_audio",
    "camera", "shot_type", "prompt_extend", "input_reference", "image",
    "images", "image_urls", "reference_urls", "callback_url", "extra",
})


def validate_h3_video_parameters(
    model_id: str, parameters: dict[str, Any], *, first: bool = False,
    last: bool = False, references: int = 0, negative_prompt: str | None = None,
) -> dict[str, Any]:
    """Validate selected inputs for an offline draft, not production authorization."""
    limits = MODEL_LIMITS.get(model_id)
    if limits is None:
        raise ConflictError("MiniMax V2 只支持官方 MiniMax-H3 和 MiniMax-H3-Max 模型 ID")
    if negative_prompt and negative_prompt.strip():
        raise ConflictError("MiniMax H3 V2 没有独立负面提示词字段")
    if any(parameters.get(key) is not None for key in UNSUPPORTED_OPTIONS):
        raise ConflictError("MiniMax H3 V2 存在未适配的扩展视频参数")
    if parameters.get("ratio") is not None and parameters["ratio"] != parameters.get("aspect_ratio"):
        raise ConflictError("MiniMax H3 V2 存在冲突的画幅参数")
    if type(references) is not int or references < 0 or references > 9:
        raise ConflictError("MiniMax H3 V2 普通参考图最多 9 张")
    if (first or last) and references:
        raise ConflictError("MiniMax H3 V2 首尾帧与普通参考图不能混用")
    duration = parameters.get("duration")
    if type(duration) is not int or not limits["minimum_duration"] <= duration <= 15:
        raise ConflictError("MiniMax H3 V2 生成时长不在当前型号支持的整数秒范围内")
    resolution = parameters.get("resolution")
    if resolution not in limits["resolutions"]:
        raise ConflictError("MiniMax H3 V2 清晰度不受当前型号支持")
    ratio = parameters.get("aspect_ratio")
    if ratio not in ASPECT_RATIOS:
        raise ConflictError("MiniMax H3 V2 需要明确的项目画幅比例")
    mode = ("first_last_frame" if first and last else "first_frame" if first or last
            else "multi_reference" if references > 1 else "single_image" if references else "text")
    declared = parameters.get("supported_video_input_modes")
    if isinstance(declared, list) and declared and mode not in declared:
        raise ConflictError("当前 MiniMax 模型配置未声明此视频输入模式")
    return {"model": model_id, "duration": duration, "resolution": resolution,
            "aspect_ratio": ratio, "input_mode": mode}
