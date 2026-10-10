"""Server-owned, account-bound audio preflight; never submits generation."""

# ruff: noqa: RUF001
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app.core.errors import ConflictError
from app.core.outbound_http import outbound_client
from app.core.provider_crypto import decrypt_api_key
from app.providers.audio_contracts import AUDIO_PROTOCOLS, validate_audio_model
from app.providers.protocols import effective_base_url, effective_protocol

KEY = "_audio_verification"
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
        "defaults": {k: v for k, v in (model.default_params or {}).items() if k != KEY},
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def read_state(provider, model):
    native = effective_protocol(provider, model) in AUDIO_PROTOCOLS
    record = (model.default_params or {}).get(KEY) or {}
    ready = False
    if native and isinstance(record, dict):
        try:
            expires = datetime.fromisoformat(record["expires_at"])
            ready = (record.get("fingerprint") == config_fingerprint(provider, model)
                     and expires.tzinfo is not None and expires > datetime.now(UTC)
                     and record.get("account_checked") is True
                     and provider.enabled and model.enabled)
        except (KeyError, TypeError, ValueError):
            pass
    return {"required": native, "ready": ready, "model_called": False,
            "checked_at": record.get("checked_at") if isinstance(record, dict) else None,
            "reason": "账户与配置只读核验通过；生成权限、效果和扣费仍以实际请求为准" if ready
            else "请先保存配置并核验音频账户；修改渠道、密钥、音色或参数后需重新核验"}


def require_verified(provider, model):
    state = read_state(provider, model)
    if state["required"] and not state["ready"]:
        raise ConflictError(state["reason"])


async def _json(client, method, url, headers, body=None):
    try:
        async with client.stream(method, url, headers=headers,
                                 **({"json": body} if body is not None else {})) as response:
            if response.status_code != 200:
                raise ConflictError(f"音频账户核验 HTTP {response.status_code}；未调用生成接口",
                                    details={"audio_account_check_failed": True})
            parts, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BODY:
                    raise ConflictError("账户核验响应过大，已停止", details={"audio_account_check_failed": True})
                parts.append(chunk)
            return json.loads(b"".join(parts))
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise ConflictError("音频账户核验未完成；未调用生成接口，也未自动重试",
                            details={"audio_account_check_failed": True}) from exc


async def verify(session, provider_id, model_id):
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            return await _verify(session, provider_id, model_id)
    except TimeoutError as exc:
        raise ConflictError("音频账户核验超时；未调用生成接口，也未自动重试",
                            details={"audio_account_check_failed": True}) from exc


async def _verify(session, provider_id, model_id):
    from app.services.planning_capability_admin import lock_catalog
    from app.services.provider_service import assert_no_active_jobs

    provider, model = await lock_catalog(session, provider_id, model_id)
    await assert_no_active_jobs(session, provider_id, model_id)
    protocol = effective_protocol(provider, model)
    if protocol not in AUDIO_PROTOCOLS:
        raise ConflictError("此核验只适用于已接入的原生音频协议")
    validate_audio_model(protocol, model.model_id)
    if not provider.enabled or not model.enabled:
        raise ConflictError("请先启用渠道和模型")
    speech = model.model_type == "tts"
    defaults = model.default_params or {}
    voices = defaults.get("voices", [])
    if speech and (not isinstance(voices, list) or not voices or len(voices) > 100
                   or any(not isinstance(v, str) or not v or len(v) > 128 for v in voices)
                   or defaults.get("voice") not in voices):
        raise ConflictError("请先登记音色列表及默认音色")
    if ("speech" if speech else "music") not in model.capabilities:
        raise ConflictError("请先登记配音或音乐能力")
    key = decrypt_api_key(provider.api_key_ciphertext)
    base = effective_base_url(provider, model).rstrip("/")
    headers = {"xi-api-key": key} if protocol.startswith("elevenlabs_") else {"Authorization": "Bearer " + key}
    async with outbound_client(timeout=min(provider.timeout_seconds, 30),
                               proxy=provider.proxy_url, follow_redirects=False) as client:
        if protocol == "minimax_audio_subscription":
            payload = await _json(client, "POST", base + "/get_voice", headers, {"voice_type": "all"})
            status = payload.get("base_resp", {}) if isinstance(payload, dict) else {}
            if not isinstance(status, dict) or type(status.get("status_code")) is not int or status["status_code"] != 0:
                raise ConflictError("MiniMax 返回账户业务错误，不能将 HTTP 200 当作核验通过",
                                    details={"audio_account_check_failed": True})
            lists = [payload.get(kind, []) for kind in ("system_voice", "voice_cloning", "voice_generation")]
            if any(not isinstance(items, list) for items in lists):
                raise ConflictError("MiniMax 音色查询结构无效", details={"audio_account_check_failed": True})
            available = {v.get("voice_id") for items in lists for v in items
                         if isinstance(v, dict) and isinstance(v.get("voice_id"), str)}
            if not set(voices) <= available:
                raise ConflictError("登记的音色不在当前账户可用音色中", details={"audio_account_check_failed": True})
        else:
            catalog = await _json(client, "GET", base + "/models", headers)
            rows = catalog.get("data", []) if isinstance(catalog, dict) else catalog
            if not isinstance(rows, list) or model.model_id not in {
                r.get("id", r.get("model_id")) for r in rows
                if isinstance(r, dict) and isinstance(r.get("id", r.get("model_id")), str)
            }:
                raise ConflictError("当前账户模型目录未返回此音频模型；不推定生成权限",
                                    details={"audio_account_check_failed": True})
            if protocol == "stepfun_tts":
                payload = await _json(client, "GET", base + "/audio/system_voices?model=" + quote(model.model_id, safe=""), headers)
                available = payload.get("voices", []) if isinstance(payload, dict) else []
                if not isinstance(available, list) or not set(voices) <= {v for v in available if isinstance(v, str)}:
                    raise ConflictError("登记音色不在当前 StepFun 模型的预设音色中",
                                        details={"audio_account_check_failed": True})
            elif protocol == "elevenlabs_tts":
                if not any(isinstance(r, dict) and r.get("model_id", r.get("id")) == model.model_id
                           and r.get("can_do_text_to_speech") is True for r in rows):
                    raise ConflictError("ElevenLabs 模型未声明配音能力", details={"audio_account_check_failed": True})
                for voice in set(voices):
                    payload = await _json(client, "GET", base + "/voices/" + quote(voice, safe=""), headers)
                    if not isinstance(payload, dict) or payload.get("voice_id") != voice:
                        raise ConflictError("ElevenLabs 音色与当前账户查询结果不一致",
                                            details={"audio_account_check_failed": True})
    now = datetime.now(UTC)
    model.default_params = {**defaults, "speech_verified" if speech else "music_verified": True}
    defaults = model.default_params
    model.default_params = {**defaults, KEY: {
        "fingerprint": config_fingerprint(provider, model), "account_checked": True,
        "checked_at": now.isoformat(), "expires_at": (now + timedelta(days=7)).isoformat(),
    }}
    await session.flush()
    return read_state(provider, model)


def model_out(provider, model):
    from app.schemas.provider import ProviderModelOut
    result = ProviderModelOut.model_validate(model)
    if effective_protocol(provider, model) in AUDIO_PROTOCOLS:
        result.audio_verification = read_state(provider, model)
    return result
