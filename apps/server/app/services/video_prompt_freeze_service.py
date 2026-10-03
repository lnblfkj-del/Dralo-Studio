"""Freeze a segment's selected prompt and M3 candidate before video submission.

The M3 candidate is selected only by an explicit, channel-verified route gate.
Existing jobs without this contract retain their historical recovery behavior.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.core.errors import ConflictError, ValidationError
from app.services.video_prompt_compiler import compile_model_prompt, resolve_model_prompt_profile

FREEZE_VERSION = "video_prompt_freeze.v1"


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def build_video_prompt_freeze(
    provider: Any, model: Any, *, script: Any, input_contract: dict[str, Any],
    active_prompt: str, voice_guidance: str = "", style_snapshot: dict[str, Any] | None = None,
    selection: str = "existing_prompt",
) -> dict[str, Any]:
    """Record the exact prompt selected after style and voice preparation."""
    profile = resolve_model_prompt_profile(provider, model, input_contract["input_mode"])
    core: dict[str, Any] = {
        "freeze_version": FREEZE_VERSION,
        "selection": selection,
        "active_prompt_sha256": _digest(active_prompt),
        "style_snapshot": style_snapshot,
        "structured_script_sha256": _digest(script) if isinstance(script, dict) else None,
        "input_fingerprint": input_contract.get("fingerprint"),
        "endpoint_fingerprint": profile["endpoint_fingerprint"],
        "route": {
            key: profile[key] for key in ("protocol", "channel_revision", "model_id", "input_mode")
        },
        "recipe": profile["recipe"],
        "verification": profile["verification"],
        "candidate_compiler_version": None,
        "candidate_fingerprint": None,
        "candidate_prompt_sha256": None,
        "candidate_status": "no_structured_script",
    }
    if isinstance(script, dict) and script.get("camera"):
        try:
            candidate = compile_model_prompt(
                script, profile=profile, input_contract=input_contract,
                voice_guidance=voice_guidance,
            )
        except (ConflictError, ValidationError) as exc:
            core["candidate_status"] = "invalid_input"
            core["candidate_issue"] = exc.message
        else:
            core.update({
                "candidate_compiler_version": candidate["compiler_version"],
                "candidate_fingerprint": candidate["fingerprint"],
                "candidate_prompt_sha256": _digest(candidate["prompt"]),
                "candidate_status": (
                    "requires_single_shot" if candidate["submission_plan"] != "single_request"
                    else "selected" if selection == "model_compiler" else "preview_only"
                ),
            })
    if selection == "model_compiler" and core["candidate_status"] != "selected":
        raise ConflictError("已启用模型提示词但正式编译结果无效")
    core["fingerprint"] = _digest(core)
    return core


def select_segment_video_prompt(
    provider: Any, model: Any, *, script: Any, input_contract: dict[str, Any],
    existing_prompt: str, existing_negative_prompt: str | None,
    source_negative_prompt: str | None, voice_guidance: str = "",
) -> tuple[str, str | None, str]:
    """Activate an M3 candidate only for an explicitly enabled, locally supported route."""
    profile = resolve_model_prompt_profile(provider, model, input_contract["input_mode"])
    if not profile["production_enabled"]:
        return existing_prompt, existing_negative_prompt, "existing_prompt"
    if profile["recipe"] in {"generic", "h3_base", "h3_ref2va"}:
        raise ConflictError("当前模型提示词未接入可提交的视频适配器")
    if not (profile.get("local_adapter_preflight") or {}).get("passed"):
        raise ConflictError("当前模型输入模式未通过本地视频适配器预检")
    if not isinstance(script, dict) or not script.get("camera"):
        raise ConflictError("模型提示词启用后需要已保存的结构化片段脚本")
    candidate = compile_model_prompt(
        script, profile=profile, input_contract=input_contract,
        voice_guidance=voice_guidance,
    )
    if candidate["submission_plan"] != "single_request":
        raise ConflictError("当前渠道没有原生多镜头提交合同, 请先拆分片段")
    if candidate["blockers"]:
        raise ConflictError("模型提示词预检未通过: " + "; ".join(candidate["blockers"]))
    if profile["recipe"].startswith("seedance_") and any(
        label["order_status"] != "adapter_order_verified"
        for label in candidate["reference_labels"]
    ):
        raise ConflictError("Seedance 参考图上传顺序未验证, 不能启用编号提示词")
    from app.services.video_input_compiler import prepare_video_prompt

    prompt, negative = prepare_video_prompt(
        provider, model, candidate["prompt"], source_negative_prompt
    )
    return prompt, negative, "model_compiler"


def assert_frozen_video_prompt(payload: dict[str, Any], provider: Any, model: Any) -> None:
    frozen = payload.get("video_prompt_freeze")
    if frozen is None:
        return
    if not isinstance(frozen, dict) or frozen.get("freeze_version") != FREEZE_VERSION:
        raise ConflictError("视频提示词冻结记录无效, 已停止提交")
    core = {key: value for key, value in frozen.items() if key != "fingerprint"}
    if frozen.get("fingerprint") != _digest(core):
        raise ConflictError("视频提示词冻结记录已变化, 已停止提交")
    if frozen.get("active_prompt_sha256") != _digest(payload.get("prompt")):
        raise ConflictError("视频提示词在费用确认后发生变化, 已停止提交")
    if frozen.get("style_snapshot") != payload.get("style_snapshot"):
        raise ConflictError("项目风格与提示词冻结记录不一致, 已停止提交")
    if frozen.get("input_fingerprint") != (payload.get("video_input_contract") or {}).get("fingerprint"):
        raise ConflictError("视频输入与提示词冻结记录不一致, 已停止提交")
    script = ((payload.get("script_snapshot") or {}).get("parameters") or {}).get("structured_script")
    if frozen.get("structured_script_sha256") != (_digest(script) if isinstance(script, dict) else None):
        raise ConflictError("片段脚本与提示词冻结记录不一致, 已停止提交")
    from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint

    if frozen.get("endpoint_fingerprint") != video_prompt_endpoint_fingerprint(provider, model):
        raise ConflictError("视频模型路由与提示词冻结记录不一致, 已停止提交")
