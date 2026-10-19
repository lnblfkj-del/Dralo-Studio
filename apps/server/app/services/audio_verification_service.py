"""Independent read-only audio checks; catalog access is not synthesis entitlement."""

# ruff: noqa: RUF001
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.core.errors import ConflictError
from app.core.outbound_http import outbound_client
from app.core.provider_crypto import decrypt_api_key
from app.providers.audio_contracts import AUDIO_METADATA_KEYS, AUDIO_PROTOCOLS, validate_audio_model
from app.providers.protocols import effective_base_url, effective_protocol

KEY = "_audio_verification"
CHECKS_KEY = "_audio_checks"
MAX_BODY = 1024 * 1024
CHECK_TIMEOUT_SECONDS = 30


def config_fingerprint(provider, model):
    # The encrypted credential never leaves this process or enters a task payload.
    config = {
        "provider_id": provider.id, "model_id": model.model_id,
        "protocol": effective_protocol(provider, model),
        "base_url": effective_base_url(provider, model),
        "account": hashlib.sha256(str(getattr(provider, "api_key_ciphertext", "")).encode()).hexdigest(),
        "proxy": getattr(provider, "proxy_url", None), "kind": model.model_type,
        "version": AUDIO_PROTOCOLS.get(effective_protocol(provider, model), {}).get("version"),
        "capabilities": model.capabilities,
        "defaults": {k: v for k, v in (model.default_params or {}).items() if k not in {KEY, *AUDIO_METADATA_KEYS}},
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def check_fingerprint(provider, model):
    identity = {"provider": provider.id, "model": model.model_id, "kind": model.model_type,
                "protocol": effective_protocol(provider, model), "base": effective_base_url(provider, model),
                "account": hashlib.sha256(str(provider.api_key_ciphertext).encode()).hexdigest(),
                "proxy": getattr(provider, "proxy_url", None)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _check(provider, model, kind):
    records = (model.default_params or {}).get(CHECKS_KEY, {})
    record = records.get(kind, {}) if isinstance(records, dict) else {}
    if not isinstance(record, dict) or record.get("fingerprint") != check_fingerprint(provider, model):
        return {"status": "unknown", "reason": "尚未检查", "checked_at": None}
    result = {key: value for key, value in record.items() if key != "fingerprint"}
    configured = (model.default_params or {}).get("voices", [])
    if kind == "voices" and isinstance(configured, list) and result.get("voice_ids") != configured:
        result = {**result, "status": "unknown", "reason": "音色清单已变化，可重新检查",
                  "missing": [voice for voice in result.get("missing", []) if voice in configured]}
    return result


def read_state(provider, model, *, require_voice=True):
    from app.providers.protocols import validate_model_protocol

    connection, voices = _check(provider, model, "connection"), _check(provider, model, "voices")
    ready, reason = True, "配置可用于提交；实际生成权限与费用以渠道返回为准"
    try:
        validate_model_protocol(provider, model)
        if model.model_type not in {"tts", "audio"}:
            raise ConflictError("请选择配音或音乐模型")
        if not provider.enabled or not model.enabled:
            raise ConflictError("请先启用渠道和模型")
        defaults = model.default_params or {}
        capability = "speech" if model.model_type == "tts" else "music"
        if capability not in model.capabilities:
            raise ConflictError("请先设置配音或音乐生成能力")
        if model.model_type == "tts" and require_voice and effective_protocol(provider, model) != "stepfun_gen":
            configured = defaults.get("voices", [])
            if (not isinstance(configured, list) or not configured or len(configured) > 100
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 255 for v in configured)
                    or (defaults.get("voice") is not None and defaults["voice"] not in configured)):
                raise ConflictError("请先填写音色 ID 并检查默认音色")
        if connection.get("authentication_failed") or voices.get("authentication_failed"):
            raise ConflictError("渠道拒绝了当前密钥，请修改密钥或重新检查连接")
    except ConflictError as exc:
        ready, reason = False, str(exc)
    return {"required": False, "ready": ready, "reason": reason, "model_called": False,
            "checks": {"connection": connection, "voices": voices}}


def require_ready(provider, model, parameters=None, *, require_voice=True):
    state = read_state(provider, model, require_voice=require_voice)
    if not state["ready"]:
        raise ConflictError(state["reason"])
    voice = (parameters or {}).get("voice", (model.default_params or {}).get("voice"))
    if require_voice and model.model_type == "tts" and voice in state["checks"]["voices"].get("missing", []):
        raise ConflictError("所选音色在最近检查中不可用，请更换音色或重新检查音色")


# Compatibility for callers of the former mandatory certificate gate.
require_verified = require_ready


class AudioCheckError(Exception):
    def __init__(self, reason, *, status="failed", authentication_failed=False):
        self.result = {"status": status, "reason": reason, "authentication_failed": authentication_failed}


def permission_error(data):
    """Only expose known permission names, never echo arbitrary upstream content."""
    detail = data.get("detail") if isinstance(data, dict) else None
    if not isinstance(detail, dict) or detail.get("status") != "missing_permissions":
        return None
    message = str(detail.get("message", ""))
    permission = next((name for name in ("models_read", "voices_read", "text_to_speech", "voice_generation", "music_generation") if name in message), None)
    return "密钥缺少 " + (permission or "此接口") + " 权限，请在 ElevenLabs 后台调整；目录查询权限不代表生成权限"


async def _json(client, method, url, headers, body=None):
    try:
        async with client.stream(method, url, headers=headers,
                                 **({"json": body} if body is not None else {})) as response:
            parts, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BODY:
                    raise AudioCheckError("查询响应过大，已停止")
                parts.append(chunk)
            raw = b"".join(parts)
            if response.status_code != 200:
                code = response.status_code
                try:
                    reason = permission_error(json.loads(raw))
                except (ValueError, UnicodeError):
                    reason = None
                error = AudioCheckError(reason or f"查询返回 HTTP {code}",
                    status="unknown" if reason or code == 403 else "unsupported" if code in {404, 405, 501} else "failed",
                    authentication_failed=code == 401 and not reason)
                if reason:
                    error.result["permission_denied"] = True
                raise error
            return json.loads(raw)
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise AudioCheckError("查询未完成，请稍后手动重试") from exc


async def verify(session, provider_id, model_id, kind="connection"):
    from app.services.planning_capability_admin import lock_catalog

    if kind not in {"connection", "voices"}:
        raise ConflictError("不支持的音频检查类型")
    provider, model = await lock_catalog(session, provider_id, model_id)
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            result = await _probe(provider, model, kind)
    except TimeoutError:
        result = {"status": "failed", "reason": "查询超时，请稍后手动重试"}
    except AudioCheckError as exc:
        result = exc.result
    previous = _check(provider, model, kind)
    if result.get("status") != "passed":
        if previous.get("authentication_failed") and not result.get("permission_denied"):
            result["authentication_failed"] = True
        if "missing" not in result and previous.get("missing"):
            result["missing"] = previous["missing"]
    defaults = model.default_params or {}
    records = defaults.get(CHECKS_KEY, {})
    records = records if isinstance(records, dict) else {}
    # Evidence never changes execution defaults or the legacy frozen job fingerprint.
    if result.get("status") == "passed" or result.get("permission_denied"):
        other = "voices" if kind == "connection" else "connection"
        if isinstance(records.get(other), dict) and records[other].get("authentication_failed"):
            records = {**records, other: {**records[other], "authentication_failed": False,
                                         "status": "unknown", "reason": "鉴权已恢复，可重新检查"}}
    if kind == "voices":
        result["voice_ids"] = defaults.get("voices", [])
    model.default_params = {**defaults, CHECKS_KEY: {**records, kind: {
        **result, "fingerprint": check_fingerprint(provider, model), "checked_at": datetime.now(UTC).isoformat()}}}
    await session.flush()
    return read_state(provider, model)


async def _probe(provider, model, kind):
    protocol = effective_protocol(provider, model)
    if model.model_type not in {"tts", "audio"}:
        raise ConflictError("此检查只适用于配音或音乐模型")
    if protocol not in AUDIO_PROTOCOLS:
        return {"status": "unsupported", "reason": "当前协议未接入此项查询，可在项目中试生成"}
    validate_audio_model(protocol, model.model_id)
    if not provider.enabled or not model.enabled:
        raise ConflictError("请先启用渠道和模型")
    speech = model.model_type == "tts"
    defaults = model.default_params or {}
    voices = defaults.get("voices", [])
    if kind == "voices":
        if protocol == "stepfun_gen":
            return {"status": "unsupported", "reason": "参考声线由描述生成，无需音色目录"}
        if not speech:
            return {"status": "unsupported", "reason": "音乐模型无需音色"}
        if (not isinstance(voices, list) or not voices or len(voices) > 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 255 for v in voices)):
            raise ConflictError("请先填写音色 ID 清单")
    key = decrypt_api_key(provider.api_key_ciphertext)
    base = effective_base_url(provider, model).rstrip("/")
    headers = {"xi-api-key": key} if protocol.startswith("elevenlabs_") else {"Authorization": "Bearer " + key}
    async with outbound_client(timeout=min(provider.timeout_seconds, 30),
                               proxy=provider.proxy_url, follow_redirects=False) as client:
        if protocol == "minimax_audio_subscription":
            payload = await _json(client, "POST", base + "/get_voice", headers, {"voice_type": "all"})
            status = payload.get("base_resp", {}) if isinstance(payload, dict) else {}
            if not isinstance(status, dict) or type(status.get("status_code")) is not int or status["status_code"] != 0:
                raise AudioCheckError("MiniMax 音色查询返回业务错误，无法确认可用性")
            if kind == "connection":
                return {"status": "passed", "reason": "账户查询成功；尚未试生成"}
            lists = [payload.get(kind, []) for kind in ("system_voice", "voice_cloning", "voice_generation")]
            if any(not isinstance(items, list) for items in lists):
                raise AudioCheckError("MiniMax 音色查询结构无效")
            available = {v.get("voice_id") for items in lists for v in items
                         if isinstance(v, dict) and isinstance(v.get("voice_id"), str)}
        elif kind == "connection":
            catalog = await _json(client, "GET", base + "/models", headers)
            rows = catalog.get("data", []) if isinstance(catalog, dict) else catalog
            if not isinstance(rows, list) or model.model_id not in {
                r.get("id", r.get("model_id")) for r in rows
                if isinstance(r, dict) and isinstance(r.get("id", r.get("model_id")), str)
            }:
                return {"status": "unknown", "reason": "目录未返回此模型，无法据此判断生成权限"}
            return {"status": "passed", "reason": "模型目录可访问；尚未试生成"}
        elif protocol == "stepfun_tts":
            payload = await _json(client, "GET", base + "/audio/system_voices?model=" + quote(model.model_id, safe=""), headers)
            available = payload.get("voices") if isinstance(payload, dict) else None
            if not isinstance(available, list):
                raise AudioCheckError("音色查询结构无效")
            available = {v for v in available if isinstance(v, str)}
        elif protocol == "elevenlabs_tts":
            available = set()
            for voice in set(voices):
                try:
                    payload = await _json(client, "GET", base + "/voices/" + quote(voice, safe=""), headers)
                except AudioCheckError as exc:
                    if exc.result["status"] == "unsupported":
                        return {"status": "unknown", "reason": "音色查询未返回结果，无法确认可用性"}
                    raise
                if not isinstance(payload, dict) or payload.get("voice_id") != voice:
                    raise AudioCheckError("音色查询结构不一致")
                available.add(voice)
        else:
            return {"status": "unsupported", "reason": "当前协议未接入音色查询"}
    missing = sorted(set(voices) - available)
    return {"status": "failed" if missing else "passed", "missing": missing,
            "reason": f"{len(missing)} 个登记音色未在查询结果中找到" if missing else "登记音色可查询；试听效果以生成结果为准"}


def model_out(provider, model):
    from app.schemas.provider import ProviderModelOut
    result = ProviderModelOut.model_validate(model)
    result.default_params = {key: value for key, value in result.default_params.items() if key != "_audio_voice_catalog"}
    if model.model_type == "video":
        from app.services.video_configuration import fingerprint

        result.video_config_fingerprint = fingerprint(model)
        from app.core.errors import ConflictError
        from app.services.video_model_settings import display_settings

        try:
            view = display_settings(model)
            result.default_params = view.default_params
            result.capabilities = view.capabilities
        except ConflictError:
            # A malformed entry must remain visible and editable in the catalog.
            pass
    if model.model_type in {"tts", "audio"}:
        result.audio_verification = read_state(provider, model)
    if model.model_type == "tts":
        from app.services.audio_voice_catalog import registered, selectable
        result.audio_voices = registered(provider, model)
        result.audio_catalog_voices = selectable(provider, model)
        result.audio_voice_scope = check_fingerprint(provider, model)
    return result
