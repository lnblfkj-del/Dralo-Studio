"""ToAPIs Kling 3 Omni image-only contract, verified 2026-09-23.

https://docs.toapis.com/docs/cn/api-reference/videos/kling-v3-omni/generation
Video and element references require separate media-resolution workflows.
"""
# Chinese punctuation is intentional in user-visible messages.
# ruff: noqa: RUF001

import re

from app.core.errors import ConflictError

MODEL = "kling-v3-omni"
IMAGE_REFERENCE = re.compile(r"<<<image_(\d+)>>>")


def prompt_with_image_references(prompt: str, *, first: bool, last: bool, references: int) -> str:
    """Keep Omni prompt references aligned with the submitted image_list."""
    count = int(first) + int(last) + references
    if not count:
        if IMAGE_REFERENCE.search(prompt):
            raise ConflictError("Omni 提示词引用了图片，但片段没有绑定参考图片")
        return prompt
    cited = [int(index) for index in IMAGE_REFERENCE.findall(prompt)]
    if cited:
        ordered = list(dict.fromkeys(cited))
        if ordered != list(range(1, count + 1)):
            raise ConflictError(f"Omni 提示词必须按顺序引用全部 {count} 张图片（<<<image_1>>> 至 <<<image_{count}>>>）")
        return prompt
    roles = (["首帧"] if first else []) + (["尾帧"] if last else []) + ["参考图"] * references
    lines = [f"- {role} {index}：<<<image_{index}>>>" for index, role in enumerate(roles, 1)]
    return f"{prompt.rstrip()}\n\n图片素材引用（按顺序对应已绑定图片）：\n" + "\n".join(lines)


def defaults() -> dict:
    return {
        "duration": 5, "durations": list(range(3, 16)),
        "resolution": "720p", "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "supports_first_frame": True, "supports_last_frame": True,
        "max_reference_images": 7, "max_image_inputs": 7,
        "supports_audio": True,
        "supported_video_input_modes": [
            "text", "first_frame", "single_image", "multi_reference", "first_last_frame",
        ],
    }


def parameters(values: dict, *, first: bool, last: bool, references: int) -> dict:
    forbidden = (
        "metadata", "image", "image_urls", "reference_images", "image_with_roles",
        "image_list", "video_list", "element_list", "video_url", "reference_urls",
        "video_with_roles", "audio_with_roles", "subjects", "size",
    )
    if any(values.get(key) is not None for key in forbidden):
        raise ConflictError("Omni 图片须通过素材引用选择；参考视频和主体元素尚未接入")
    if last and not first:
        raise ConflictError("Omni 尾帧必须同时提供首帧")
    count = int(first) + int(last) + references
    if count > 7:
        raise ConflictError(
            f"Omni 实际图片输入 {count} 张（含首尾帧和风格图），上限 7 张；"
            "请调整参考图或先合成镜头首帧，已保存的片段脚本不受影响"
        )
    duration = values.get("duration", 5)
    if type(duration) is float and duration.is_integer():
        duration = int(duration)
    if type(duration) is not int or duration not in range(3, 16):
        raise ConflictError("Omni 时长必须为 3-15 秒整数")
    resolution = values.get("resolution")
    if resolution in (None, "default", "模型默认"):
        resolution = "1080p" if values.get("mode") == "pro" else "720p"
    resolution = {"std": "720p", "pro": "1080p"}.get(str(resolution).lower(), str(resolution).lower())
    if resolution not in {"720p", "1080p"}:
        raise ConflictError("当前 ToAPIs Omni 接口仅支持 720P、1080P")
    mode = "pro" if resolution == "1080p" else "std"
    if values.get("mode") not in (None, mode):
        raise ConflictError("Omni 模式与清晰度不一致：std 对应 720P，pro 对应 1080P")
    ratio = values.get("aspect_ratio") or "16:9"
    if ratio not in {"16:9", "9:16", "1:1"}:
        raise ConflictError("Omni 画幅仅支持 16:9、9:16、1:1")
    audio = values.get("audio", values.get("generate_audio", False))
    if type(audio) is not bool or (
        "generate_audio" in values and (type(values["generate_audio"]) is not bool or values["generate_audio"] != audio)
    ):
        raise ConflictError("Omni 声音参数必须为一致的布尔值")
    result = {**values, "duration": duration, "resolution": resolution,
              "mode": mode, "aspect_ratio": ratio, "audio": audio}
    if "watermark" in values and type(values["watermark"]) is not bool:
        raise ConflictError("watermark 必须为布尔值")
    return result
