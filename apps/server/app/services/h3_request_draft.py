"""Build a non-submittable MiniMax V2 request draft from frozen local inputs."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from app.core.errors import ConflictError, ValidationError
from app.providers.minimax_video_parameters import ASPECT_RATIOS, MODEL_LIMITS
from app.services.h3_prompt_validator import validate_h3_authored_prompt
from app.services.video_prompt_compiler import compile_model_prompt

REQUEST_DRAFT_VERSION = "h3_request_draft.v1"
SOURCE_URL = "https://platform.minimax.cn/docs/api-reference/video-generation-v2-create"
RATIOS = ASPECT_RATIOS | {"adaptive"}


def _image_metadata(media_id: int, media: dict[int, dict[str, Any]]) -> tuple[str, float]:
    item = media.get(media_id)
    if not isinstance(item, dict):
        raise ValidationError(f"媒体 {media_id} 缺少本次请求的冻结素材资料")
    url = item.get("url")
    if not isinstance(url, str):
        raise ValidationError(f"媒体 {media_id} 缺少可供渠道访问的 HTTPS 地址")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValidationError(f"媒体 {media_id} 需要不含用户信息和片段标识的 HTTPS 地址")
    width, height, size = (item.get(key) for key in ("width", "height", "size_bytes"))
    if any(type(value) is not int for value in (width, height, size)):
        raise ValidationError(f"媒体 {media_id} 缺少可验证的宽高或字节数")
    ratio = width / height
    if not 256 <= width <= 5760 or not 256 <= height <= 5760 or not 0.4 <= ratio <= 2.5:
        raise ValidationError(f"媒体 {media_id} 尺寸或画幅超出 H3 官方图片限制")
    if not 0 < size <= 30 * 1024 * 1024:
        raise ValidationError(f"媒体 {media_id} 大小超出 H3 官方图片限制")
    return url, ratio


def _ratio_value(value: str) -> float:
    left, right = value.split(":")
    return int(left) / int(right)


def build_h3_request_draft(
    script: dict[str, Any], *, profile: dict[str, Any], input_contract: dict[str, Any],
    authored_prompt: str, media: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a local JSON body; no provider, task, or billing calls occur."""
    model_id = profile.get("model_id")
    if profile.get("protocol") != "minimax_video_v2" or model_id not in MODEL_LIMITS:
        raise ConflictError("仅支持官方 MiniMax V2 协议及精确的 H3/H3-Max 模型 ID 草案")
    if input_contract.get("ready") is not True or input_contract.get("blockers") or input_contract.get("required_confirmations"):
        raise ConflictError("冻结视频输入仍有冲突或待确认事项")
    if not isinstance(script.get("camera"), list) or len(script["camera"]) != 1:
        raise ConflictError("当前渠道未认证原生多镜头; 请先拆成独立单镜头任务")
    params = input_contract.get("effective_parameters") or {}
    duration, resolution = params.get("duration"), params.get("resolution")
    limits = MODEL_LIMITS[model_id]
    if type(duration) is not int or not limits["minimum_duration"] <= duration <= 15:
        raise ValidationError("H3 模型生成时长须为该型号支持的整数秒")
    if not isinstance(resolution, str) or resolution not in limits["resolutions"]:
        raise ValidationError("H3 模型清晰度不受当前型号支持")
    ratio = params.get("aspect_ratio")
    if "ratio" in params and params["ratio"] != ratio:
        raise ValidationError("画幅参数存在两个冲突来源")
    if not isinstance(ratio, str) or ratio not in RATIOS:
        raise ValidationError("H3 请求缺少明确且受支持的画幅")

    compiled = compile_model_prompt(script, profile=profile, input_contract=input_contract,
                                    require_submission=True)
    validation = validate_h3_authored_prompt(
        authored_prompt, script=script, input_contract=input_contract,
        reference_labels=compiled["reference_labels"], recipe=profile["recipe"],
    )
    if not validation["format_valid"]:
        raise ConflictError("H3 正式提示词格式未通过: " + ", ".join(item["code"] for item in validation["issues"]))
    if not authored_prompt.strip() or len(authored_prompt) > 7000:
        raise ValidationError("H3 正式提示词须为 1-7000 字符")

    refs = compiled["reference_labels"]
    if len(refs) != len(input_contract["effective_references"]):
        raise ConflictError("H3 引用标签与冻结媒体数量不一致")
    if len([item for item in refs if item["role"] == "reference_image"]) > 9:
        raise ValidationError("H3 普通参考图最多 9 张")
    if not refs and ratio == "adaptive":
        raise ValidationError("H3 文生视频必须使用具体画幅")

    content: list[dict[str, Any]] = [{"type": "text", "text": authored_prompt}]
    frame_ratios = []
    for ref in refs:
        url, image_ratio = _image_metadata(ref["media_id"], media or {})
        content.append({"type": "image_url", "image_url": {"url": url}, "role": ref["role"]})
        if ref["role"] in {"first_frame", "last_frame"}:
            frame_ratios.append(image_ratio)
    if frame_ratios:
        if max(frame_ratios) - min(frame_ratios) > 0.02:
            raise ValidationError("首尾帧画幅不一致, 不能让渠道隐式裁切")
        if ratio != "adaptive" and abs(frame_ratios[0] - _ratio_value(ratio)) > 0.02:
            raise ValidationError("首尾帧实际画幅与项目画幅不一致; 渠道会忽略请求中的画幅值")
        ratio = "adaptive"
    body = {"model": model_id, "content": content, "resolution": resolution,
            "duration": duration, "ratio": ratio}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 64 * 1024 * 1024:
        raise ValidationError("H3 请求体超过官方 64 MB 上限")
    return {
        "draft_version": REQUEST_DRAFT_VERSION,
        "source_url": SOURCE_URL,
        "body": body,
        "body_sha256": sha256(raw).hexdigest(),
        "prompt_format_validation": validation,
        "reference_labels": refs,
        "input_fingerprint": input_contract.get("fingerprint"),
        "ready_for_submission": False,
        "blockers": ["仅为离线请求草案; 未接入媒体可达性、价格、任务冻结和真实渠道验收"],
    }
