"""Protocol-authoritative compilation of frozen video media inputs."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.core.errors import ConflictError
from app.providers.protocols import effective_protocol, is_toapis_model
from app.providers.video_contracts import VIDEO_CONTRACTS, validate_video_parameters


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _normalize_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parameters)
    duration = normalized.get("duration")
    if type(duration) is float and duration.is_integer():
        normalized["duration"] = int(duration)
    return normalized


def _confirmation_id(role: str, media_id: int) -> str:
    return f"{role}_to_reference:{media_id}"


def prepare_video_prompt(provider, model, prompt: str, negative_prompt: str | None) -> tuple[str, str | None]:
    """Map prompt constraints to fields actually supported by the upstream protocol."""
    negative = (negative_prompt or "").strip()
    if negative and is_toapis_model(provider, model) and model.model_id not in {"wan2.6", "kling-v2-6"}:
        return f"{prompt.rstrip()}\n\n生成限制（必须遵守）：{negative}", None
    return prompt, negative_prompt


def _validate(provider, model, parameters, negative_prompt, first, last, references,
              *, preview_only=False, h3_authorized=False) -> dict[str, Any]:
    protocol = effective_protocol(provider, model)
    effective = {**(model.default_params or {}), **parameters}
    if protocol == "minimax_video_v2":
        if not preview_only and not h3_authorized:
            raise ConflictError("MiniMax H3 生产提交尚未开放；请先完成片段英文提示词审核")
        from app.providers.minimax_video_parameters import validate_h3_video_parameters

        validate_h3_video_parameters(
            model.model_id, effective, negative_prompt=negative_prompt,
            first=bool(first), last=bool(last), references=len(references),
        )
        return effective
    if protocol == "fake_video":
        return effective
    if protocol in VIDEO_CONTRACTS:
        validate_video_parameters(protocol, effective, negative_prompt=negative_prompt,
                                  first=bool(first), last=bool(last), references=len(references))
        return effective
    if is_toapis_model(provider, model):
        from app.providers.toapis import video_parameters
        if negative_prompt and model.model_id not in {"wan2.6", "kling-v2-6"}:
            raise ConflictError("此 ToAPIs 视频协议未声明负面提示词，请合并到生成要求中")
        return video_parameters(model.model_id, effective, first=bool(first), last=bool(last), references=len(references))
    if first or last or references:
        raise ConflictError("当前视频适配器没有已注册的图像输入协议")
    return effective


def _fields(references: list[dict[str, Any]]) -> tuple[int | None, int | None, list[int]]:
    frames: dict[str, list[int]] = {"first_frame": [], "last_frame": []}
    ordinary: list[int] = []
    for ref in references:
        role, media_id = ref["role"], ref["media_id"]
        (frames[role] if role in frames else ordinary).append(media_id)
    if any(len(set(values)) > 1 for values in frames.values()):
        raise ConflictError("首帧和尾帧各只能选择一个精确媒体版本")
    return (frames["first_frame"][0] if frames["first_frame"] else None,
            frames["last_frame"][0] if frames["last_frame"] else None,
            list(dict.fromkeys(ordinary)))


def _mode(first: int | None, last: int | None, references: list[int]) -> str:
    if first and last:
        return "first_last_frame"
    if first or last:
        return "first_frame"
    if len(references) > 1:
        return "multi_reference"
    return "single_image" if references else "text"


def _submission_mode(provider, model, input_mode: str) -> str:
    """Return the exact upstream mode when the channel requires one."""
    if (
        effective_protocol(provider, model) == "openai_compatible"
        and is_toapis_model(provider, model)
        and model.model_id == "grok-video-1.5"
    ):
        return {
            "text": "text_to_video",
            "first_frame": "first_frame_image_to_video",
            "single_image": "reference_images_to_video",
            "multi_reference": "reference_images_to_video",
        }[input_mode]
    return input_mode


def compile_video_input(provider, model, references: list[dict[str, Any]], parameters: dict[str, Any], *,
                        negative_prompt: str | None = None,
                        confirmed_downgrades: list[str] | None = None,
                        preview_only: bool = False, h3_authorized: bool = False) -> dict[str, Any]:
    """Compile roles to exact adapter fields without silently dropping a binding."""
    original_duration = parameters.get("duration")
    parameters = _normalize_parameters(parameters)
    style_in_first_frame = parameters.pop("project_style_in_first_frame", False)
    if type(style_in_first_frame) is not bool:
        raise ConflictError("首帧已包含项目风格的确认必须为布尔值")
    confirmed = set(confirmed_downgrades or [])
    normalized, seen = [], set()
    for raw in references:
        media_id = raw.get("media_id", raw.get("media_file_id"))
        role = raw.get("role", "reference_image")
        if type(media_id) is not int or media_id <= 0 or role not in {"reference_image", "first_frame", "last_frame"}:
            raise ConflictError("视频输入必须包含有效媒体 ID 和首帧、尾帧或参考图用途")
        if (media_id, role) not in seen:
            normalized.append({**raw, "media_id": media_id, "role": role})
            seen.add((media_id, role))

    protocol = effective_protocol(provider, model)
    if preview_only and protocol != "minimax_video_v2":
        raise ConflictError("离线预览参数合同仅适用于 MiniMax H3 V2")
    actions, blockers, required = [], [], []
    if style_in_first_frame:
        if not any(item["role"] == "first_frame" for item in normalized):
            raise ConflictError("仅在已选择首帧时可以确认项目风格已融入首帧")
        style_refs = [item for item in normalized
                      if item.get("purpose") == "project_style" and item["role"] == "reference_image"]
        normalized = [item for item in normalized if item not in style_refs]
        if style_refs:
            actions.append({"source": "project_style", "status": "baked_into_first_frame",
                            "count": len(style_refs), "media_ids": [item["media_id"] for item in style_refs],
                            "reason": "已明确确认风格融入首帧，保留风格文本，不重复附加风格图片"})
    if model.model_id == "kling-v3-omni" and len(normalized) > 7 and is_toapis_model(provider, model):
        # Only the automatically appended style image is optional. Never remove
        # authored character/scene/prop references to make a request fit.
        style_refs = [item for item in normalized if item.get("purpose") == "project_style"]
        if style_refs:
            normalized = [item for item in normalized if item not in style_refs]
            actions.append({"source": "project_style", "status": "text_only",
                            "count": len(style_refs), "media_ids": [item["media_id"] for item in style_refs],
                            "reason": "Omni 图片名额优先用于片段资产；保留风格文字，不附加风格图片"})
    effective_refs = normalized
    try:
        first, last, ordinary = _fields(effective_refs)
        effective_parameters = _validate(provider, model, parameters, negative_prompt, first, last, ordinary,
                                         preview_only=preview_only, h3_authorized=h3_authorized)
    except ConflictError as direct_error:
        frame_refs = [item for item in normalized if item["role"] in {"first_frame", "last_frame"}]
        converted = [{**item, "role": "reference_image", "downgraded_from": item["role"]}
                     if item in frame_refs else item for item in normalized]
        convertible = bool(frame_refs)
        try:
            c_first, c_last, c_ordinary = _fields(converted)
            converted_parameters = _validate(provider, model, parameters, negative_prompt,
                                             c_first, c_last, c_ordinary, preview_only=preview_only,
                                             h3_authorized=h3_authorized)
        except ConflictError:
            convertible = False
        if not convertible:
            blockers.append(direct_error.message)
            first, last, ordinary = _fields(normalized)
            effective_parameters = {**(model.default_params or {}), **parameters}
        else:
            required = [{"id": _confirmation_id(item["role"], item["media_id"]),
                         "source": item["role"], "media_id": item["media_id"],
                         "reason": "模型协议不支持该帧位，确认后仅将这张图片改作普通参考图"}
                        for item in frame_refs]
            valid_ids = {item["id"] for item in required}
            if confirmed - valid_ids:
                blockers.append("视频输入降级确认已失效，请重新预检")
            if valid_ids - confirmed:
                blockers.append("存在需要明确确认的图像用途降级")
                first, last, ordinary = _fields(normalized)
                effective_parameters = {**(model.default_params or {}), **parameters}
            else:
                effective_refs, first, last, ordinary = converted, c_first, c_last, c_ordinary
                effective_parameters = converted_parameters
                actions.extend({"source": item["role"], "status": "degraded", "count": 1,
                                "confirmation_id": _confirmation_id(item["role"], item["media_id"]),
                                "reason": "已按用户确认改作普通参考图"} for item in frame_refs)
                required = []

    max_refs = (model.default_params or {}).get("max_reference_images")
    if type(max_refs) is int and len(ordinary) > max_refs:
        blockers.append(f"普通参考图 {len(ordinary)} 张，超过模型配置上限 {max_refs} 张")
    for role, count in (("first_frame", int(bool(first))), ("last_frame", int(bool(last))),
                        ("reference_image", len(ordinary))):
        if count and not any(item.get("source") == role for item in actions):
            actions.append({"source": role, "status": "sent", "count": count,
                            "reason": "按协议原生输入字段发送"})

    protocol_version = VIDEO_CONTRACTS.get(protocol)
    if protocol_version is None:
        protocol_version = "toapis.video.v1" if is_toapis_model(provider, model) else protocol
    if (
        type(original_duration) is float
        and original_duration.is_integer()
        and effective_parameters.get("duration") == int(original_duration)
        and protocol != "minimax_video_v2"
    ):
        effective_parameters["duration"] = original_duration
    input_mode = _mode(first, last, ordinary)
    core = {"schema_version": "video_input_contract.v2", "protocol": protocol,
            "protocol_version": protocol_version,
            "provider_model_id": model.id, "model_id": model.model_id,
            "input_mode": input_mode,
            "submission_mode": _submission_mode(provider, model, input_mode),
            "first_frame_media_id": first,
            "last_frame_media_id": last, "reference_media_ids": ordinary,
            "confirmed_downgrades": sorted(confirmed)}
    if preview_only:
        core["preview_only"] = True
    core["fingerprint"] = _digest(core)
    return {**core, "ready": not blockers and not required, "effective_references": effective_refs,
            "effective_parameters": effective_parameters, "actions": actions,
            "blockers": list(dict.fromkeys(blockers)), "required_confirmations": required}


def assert_frozen_video_input(payload: dict[str, Any]) -> dict[str, Any]:
    contract = payload.get("video_input_contract")
    version = contract.get("schema_version") if isinstance(contract, dict) else None
    if version not in {"video_input_contract.v1", "video_input_contract.v2"}:
        raise ConflictError("视频任务缺少冻结的输入协议，请重新预检并创建任务")
    if contract.get("preview_only"):
        raise ConflictError("离线预览输入不能用于视频生产任务")
    expected = {"first_frame_media_id": payload.get("first_frame_media_id"),
                "last_frame_media_id": payload.get("last_frame_media_id"),
                "reference_media_ids": payload.get("reference_media_ids", [])}
    if any(contract.get(key) != value for key, value in expected.items()):
        raise ConflictError("视频任务媒体字段与冻结输入协议不一致，已停止提交")
    keys = ["schema_version", "protocol", "protocol_version", "provider_model_id",
            "model_id", "input_mode"]
    if version == "video_input_contract.v2":
        keys.append("submission_mode")
    keys.extend(["first_frame_media_id", "last_frame_media_id",
                 "reference_media_ids", "confirmed_downgrades"])
    core = {key: contract.get(key) for key in keys}
    if contract.get("fingerprint") != _digest(core):
        raise ConflictError("视频任务冻结输入协议指纹无效，已停止提交")
    return contract
