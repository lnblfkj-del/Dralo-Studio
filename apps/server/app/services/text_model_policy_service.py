"""Text-only model limits, request parameters and frozen execution policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.errors import ModelOutputTruncatedError, TimeoutError_, ValidationError
from app.models import MODEL_TYPE_TEXT, Provider, ProviderModel

POLICY_KEY = "_text_execution"

_LIMITS = {
    "max_output_tokens": (256, 200_000),
    "request_timeout_seconds": (5, 3_600),
    "first_byte_timeout_seconds": (5, 600),
    "stream_idle_timeout_seconds": (5, 900),
}
_REASONING_EFFORTS = {"auto", "low", "medium", "high"}


def validate_defaults(model_type: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Validate the private text policy without changing unrelated model defaults."""
    normalized = dict(defaults or {})
    raw = normalized.get(POLICY_KEY)
    if raw is None:
        return normalized
    if model_type != MODEL_TYPE_TEXT:
        raise ValidationError("输出预算和文本超时只能配置在文本模型中")
    if not isinstance(raw, dict):
        raise ValidationError("文本执行策略必须是 JSON 对象")
    unknown = set(raw) - set(_LIMITS) - {"reasoning_effort"}
    if unknown:
        raise ValidationError(f"文本执行策略包含未知字段：{sorted(unknown)[0]}")
    policy: dict[str, int | str] = {}
    for key, value in raw.items():
        if key == "reasoning_effort":
            if not isinstance(value, str) or value not in _REASONING_EFFORTS:
                raise ValidationError("思考等级必须为自动、低、中或高")
            policy[key] = value
            continue
        minimum, maximum = _LIMITS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValidationError(f"{key} 必须在 {minimum}—{maximum} 之间")
        policy[key] = value
    if policy.get("first_byte_timeout_seconds", 0) > policy.get(
        "request_timeout_seconds", 3_600
    ):
        raise ValidationError("首响应等待不能超过单次调用总超时")
    if policy.get("stream_idle_timeout_seconds", 0) > policy.get(
        "request_timeout_seconds", 3_600
    ):
        raise ValidationError("流式空闲超时不能超过单次调用总超时")
    output_aliases = [
        key for key in ("max_tokens", "max_completion_tokens") if key in normalized
    ]
    if len(output_aliases) > 1:
        raise ValidationError("默认参数不能同时设置 max_tokens 与 max_completion_tokens")
    if output_aliases:
        default_output = normalized[output_aliases[0]]
        if not isinstance(default_output, int) or isinstance(default_output, bool) or default_output <= 0:
            raise ValidationError("默认输出预算必须是正整数")
        if policy.get("max_output_tokens") and default_output > policy["max_output_tokens"]:
            raise ValidationError("默认输出预算不能超过单次最大输出预算")
    normalized[POLICY_KEY] = policy
    return normalized


def resolve(
    model: ProviderModel,
    provider: Provider,
    requested: dict[str, Any],
    execution_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge parameters, cap output budget and return an audit-safe policy snapshot."""
    defaults = validate_defaults(model.model_type, model.default_params or {})
    configured = dict(defaults.pop(POLICY_KEY, {}) or {})
    requested = dict(requested or {})
    requested_aliases = [
        key for key in ("max_tokens", "max_completion_tokens") if key in requested
    ]
    default_aliases = [
        key for key in ("max_tokens", "max_completion_tokens") if key in defaults
    ]
    if len(requested_aliases) > 1 or len(default_aliases) > 1:
        raise ValidationError("max_tokens 与 max_completion_tokens 不能同时设置")
    protocol = getattr(model, "api_protocol", None) or getattr(provider, "protocol", None)
    reasoning_model = "reasoning" in (getattr(model, "capabilities", None) or [])
    openai_text_protocol = protocol in {"openai_compatible", "newapi"}
    output_key = (
        "max_completion_tokens" if openai_text_protocol and reasoning_model
        else default_aliases[0] if default_aliases else "max_tokens"
    )
    requested_output = defaults.get(default_aliases[0]) if default_aliases else None
    for key in ("max_tokens", "max_completion_tokens"):
        defaults.pop(key, None)
        requested.pop(key, None)
    for key in ("temperature", "reasoning_effort", "maxOutputTokens", "thinking", "thinkingConfig"):
        requested.pop(key, None)
    parameters = {**defaults, **requested}
    reasoning_effort = configured.get("reasoning_effort", parameters.pop("reasoning_effort", "auto"))
    parameters.pop("reasoning_effort", None)
    if reasoning_effort != "auto":
        if not openai_text_protocol or not reasoning_model:
            raise ValidationError("当前模型或协议不支持手动思考等级，请在模型管理中选择自动")
        parameters["reasoning_effort"] = reasoning_effort
    if protocol == "anthropic_messages":
        output_key = "max_tokens"
    if requested_output is not None and (
        not isinstance(requested_output, int)
        or isinstance(requested_output, bool)
        or requested_output <= 0
    ):
        raise ValidationError(f"{output_key} 必须是正整数")
    ceiling = configured.get("max_output_tokens")
    model_budget = (requested.get("agent_workspace") == "script_asset_breakdown_batch"
                    and requested.get("output_budget_mode") == "model")
    if model_budget:
        # Asset extraction has no business-level output cap. Prefer the configured
        # model capacity; otherwise omit optional output limits at the provider boundary.
        effective_output = ceiling
        parameters.pop("maxOutputTokens", None)
        if (getattr(model, "api_protocol", None) or getattr(provider, "protocol", None)) == "anthropic_messages":
            effective_output = ceiling or requested_output
            if effective_output is None:
                raise ValidationError("此渠道要求显式输出参数，请先在文本模型中配置其支持的最大输出 Token；资产拆解不使用固定默认限额")
        requested_output = None
    else:
        effective_output = min(requested_output, ceiling) if requested_output and ceiling else (
            requested_output or ceiling
        )
    if effective_output is not None:
        parameters[output_key] = effective_output
    elif protocol == "anthropic_messages":
        raise ValidationError("Anthropic 文本模型要求在模型管理中配置默认输出预算或单次最大输出预算")

    global_timeouts = dict(execution_snapshot.get("timeouts") or {})
    request_timeout = configured.get("request_timeout_seconds", provider.timeout_seconds)
    first_byte = configured.get(
        "first_byte_timeout_seconds", global_timeouts.get("first_byte_seconds", 120)
    )
    stream_idle = configured.get(
        "stream_idle_timeout_seconds", global_timeouts.get("stream_idle_seconds", 180)
    )
    first_byte = min(int(first_byte), int(request_timeout))
    stream_idle = min(int(stream_idle), int(request_timeout))
    frozen = {
        "model_id": model.id,
        "model_identifier": model.model_id,
        "output_parameter": output_key,
        "requested_output_tokens": requested_output,
        "max_output_tokens": ceiling,
        "effective_output_tokens": effective_output,
        "reasoning_effort": reasoning_effort,
        "request_timeout_seconds": int(request_timeout),
        "first_byte_timeout_seconds": first_byte,
        "stream_idle_timeout_seconds": stream_idle,
        "sources": {
            "output": ("model_capacity" if effective_output is not None else "provider_default")
            if model_budget else "model_ceiling" if ceiling is not None else "model_default_or_provider",
            "request_timeout": "model" if "request_timeout_seconds" in configured else "provider",
            "first_byte_timeout": "model" if "first_byte_timeout_seconds" in configured else "execution_policy",
            "stream_idle_timeout": "model" if "stream_idle_timeout_seconds" in configured else "execution_policy",
        },
    }
    return parameters, frozen


def frozen_for(job, provider: Provider) -> dict[str, Any]:
    """Read a task's text limits, with a bounded fallback for legacy jobs."""
    frozen = dict((job.execution_policy_snapshot or {}).get("text_model") or {})
    if frozen:
        return frozen
    timeouts = dict((job.execution_policy_snapshot or {}).get("timeouts") or {})
    return {
        "request_timeout_seconds": provider.timeout_seconds,
        "first_byte_timeout_seconds": min(
            provider.timeout_seconds, int(timeouts.get("first_byte_seconds", 120))
        ),
        "stream_idle_timeout_seconds": min(
            provider.timeout_seconds, int(timeouts.get("stream_idle_seconds", 180))
        ),
    }


def inherit_snapshot(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Inherit batch scheduling, but keep the child's actual resolved model settings."""
    return {
        **child,
        **parent,
        "text_model": dict(child.get("text_model") or parent.get("text_model") or {}),
    }


def inherit_job_snapshot(parent, child) -> None:
    child.execution_policy_snapshot = inherit_snapshot(
        dict(parent.execution_policy_snapshot or {}),
        dict(child.execution_policy_snapshot or {}),
    )


def ensure_complete_result(
    result: dict[str, Any],
    *,
    submission: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> None:
    """Reject a provider-confirmed limit or interrupted stream before any local write."""
    submission = submission or {}
    finish_reason = str(result.get("finish_reason") or submission.get("finish_reason") or "").lower()
    budget = (policy or {}).get("effective_output_tokens")
    if finish_reason in {"length", "max_tokens", "max_output_tokens", "max_output", "max_completion_tokens"}:
        message = (
            "本次请求未设置明确输出预算，沿用渠道默认限制；渠道返回 length/输出上限，正文被截断。请在模型管理中配置默认输出预算后重新生成"
            if budget is None else
            f"本次请求输出预算为 {budget} tokens，渠道仍报告达到输出上限；请核对思考消耗及渠道是否支持并执行该预算，再调整模型设置重新生成"
        )
        raise ModelOutputTruncatedError(
            message,
            details={"finish_reason": finish_reason, "output_budget_tokens": budget},
        )
    terminal_seen = result.get("stream_terminal_seen", submission.get("stream_terminal_seen"))
    if terminal_seen is False:
        raise ModelOutputTruncatedError(
            "模型流式响应未收到结束标记，可能由渠道或连接提前中断；已保存收到的内容，但无法确认输出预算是否不足",
            details={"stream_terminal_seen": False, "output_budget_tokens": budget},
        )


async def execute(
    adapter,
    *,
    model: str,
    prompt: str,
    parameters: dict[str, Any],
    policy: dict[str, Any],
    streaming: bool = False,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, object]:
    """Apply a real wall-clock deadline and optional streaming idle limits."""
    call_parameters = dict(parameters)
    if hasattr(adapter, "timeout_seconds"):
        adapter.timeout_seconds = int(policy["request_timeout_seconds"])
    if streaming:
        call_parameters["_text_first_byte_timeout_seconds"] = int(
            policy["first_byte_timeout_seconds"]
        )
        call_parameters["_text_stream_idle_timeout_seconds"] = int(
            policy["stream_idle_timeout_seconds"]
        )
    try:
        async with asyncio.timeout(int(policy["request_timeout_seconds"])):
            if streaming:
                return await adapter.generate_text_stream(
                    model=model,
                    prompt=prompt,
                    parameters=call_parameters,
                    on_chunk=on_chunk,
                )
            return await adapter.generate_text(
                model=model, prompt=prompt, parameters=call_parameters
            )
    except TimeoutError as exc:
        raise TimeoutError_("文本模型单次调用超过配置的总超时") from exc
