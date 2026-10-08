"""Provider HTTP error normalization with bounded, credential-safe diagnostics."""

import json
import re
from typing import Any

import httpx

from app.core.errors import (
    ProviderAuthError,
    ProviderCapacityError,
    ProviderEndpointError,
    ProviderError,
    ProviderMethodError,
    ProviderModelNotFoundError,
    ProviderParameterError,
    QuotaError,
    RateLimitError,
)

_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-goog-request-id",
    "x-amzn-requestid",
    "x-log-id",
    "x-tt-logid",
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_MODEL_NOT_FOUND_MARKERS = (
    "model not found",
    "model_not_found",
    "unknown model",
    "invalid model",
    "does not exist",
    "未找到模型",
    "模型不存在",
)
_STREAM_UNSUPPORTED_MARKERS = (
    "stream_options is not supported",
    "stream_options not supported",
    "unsupported parameter: stream_options",
    "unknown parameter: stream_options",
    "unrecognized request argument supplied: stream_options",
    "streaming is not supported",
    "stream is not supported",
)


def _safe_token(value: Any, *, limit: int = 160) -> str | None:
    text = str(value or "").strip()[:limit]
    return text if text and _SAFE_TOKEN.fullmatch(text) else None


def _error_payload(response: httpx.Response) -> tuple[str | None, str]:
    try:
        payload = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    error = payload.get("error")
    if isinstance(error, dict):
        code = _safe_token(error.get("code") or error.get("type"))
        message = error.get("message")
    else:
        code = _safe_token(payload.get("code") or payload.get("type"))
        message = payload.get("message") or error
    return code, str(message or "")[:1000].lower()


def provider_http_diagnostic(response: httpx.Response) -> dict[str, Any]:
    """Return allowlisted metadata only; never include body, query or credentials."""
    code, _message = _error_payload(response)
    details: dict[str, Any] = {"http_status": response.status_code}
    try:
        request = response.request
    except RuntimeError:
        request = None
    if request is not None:
        details["endpoint_host"] = request.url.host
        details["endpoint_path"] = request.url.path[:300]
    for header in _REQUEST_ID_HEADERS:
        request_id = _safe_token(response.headers.get(header))
        if request_id:
            details["request_id"] = request_id
            break
    if code:
        details["provider_error_code"] = code
    return details


def safe_parameter_message(response: httpx.Response) -> str:
    """Retain useful validation errors without echoing credentials or input media."""
    _code, message = _error_payload(response)
    if not message:
        return ""
    try:
        request = response.request
        for header in ("authorization", "x-api-key", "api-key", "x-goog-api-key"):
            token = request.headers.get(header, "")
            if header == "authorization":
                token = token.partition(" ")[2]
            if token:
                message = message.replace(token.lower(), "[redacted]")
        try:
            submitted = json.loads(request.content)
        except (ValueError, httpx.RequestNotRead):
            submitted = {}
        if isinstance(submitted, dict):
            for key in ("prompt", "negative_prompt"):
                value = submitted.get(key)
                if isinstance(value, str) and value:
                    message = message.replace(value.lower(), "[input]")
            for item in submitted.get("messages", []) if isinstance(submitted.get("messages"), list) else []:
                content = item.get("content") if isinstance(item, dict) else None
                if isinstance(content, str) and content:
                    message = message.replace(content.lower(), "[input]")
    except RuntimeError:
        pass
    message = re.sub(r"data:[^\s\"']+", "[media]", message)
    message = re.sub(r"https?://[^\s\"'<>]+", "[url]", message)
    message = re.sub(r"\b(?:sk-[a-z0-9_-]+|bearer\s+[^\s,;]+)", "[redacted]", message)
    message = re.sub(r"(?:api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+", "[redacted]", message)
    return " ".join(message.split())[:300]


def is_explicit_stream_unsupported(response: httpx.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    code, message = _error_payload(response)
    haystack = f"{code or ''} {message}".lower()
    return any(marker in haystack for marker in _STREAM_UNSUPPORTED_MARKERS)


def raise_for_provider_http_error(response: httpx.Response) -> None:
    if not response.is_error and not 300 <= response.status_code < 400:
        return
    details = provider_http_diagnostic(response)
    explanation = safe_parameter_message(response)
    if explanation:
        details["provider_error_message"] = explanation
    code, message = _error_payload(response)
    haystack = f"{code or ''} {message}".lower()
    status = response.status_code
    if status == 503 and "no available image quota" in message:
        raise ProviderCapacityError(details=details)
    if status in {400, 415, 422}:
        explanation = safe_parameter_message(response)
        if explanation:
            details["provider_error_message"] = explanation
        raise ProviderParameterError(
            f"渠道拒绝请求参数 (HTTP {status}): {explanation}" if explanation else None,
            details=details,
        )
    if 300 <= status < 400:
        raise ProviderEndpointError(
            "渠道返回重定向，请填写最终 API 地址；为保护密钥未自动跟随",
            details=details,
        )
    if status in {401, 403}:
        raise ProviderAuthError(details=details)
    if status == 402:
        raise QuotaError(details=details)
    if status == 404:
        model_missing = (
            "model" in str(code or "").lower()
            or (
                "model" in message
                and any(marker in haystack for marker in _MODEL_NOT_FOUND_MARKERS)
            )
            or "未找到模型" in message
            or "模型不存在" in message
        )
        if model_missing:
            raise ProviderModelNotFoundError(details=details)
        raise ProviderEndpointError(details=details)
    if status == 405:
        raise ProviderMethodError(details=details)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        try:
            retry_after_seconds = max(1, int(float(retry_after))) if retry_after else None
        except ValueError:
            retry_after_seconds = None
        raise RateLimitError(
            retry_after_seconds=retry_after_seconds,
            details=details,
        )
    raise ProviderError(f"模型渠道返回 HTTP {status}", details=details)
