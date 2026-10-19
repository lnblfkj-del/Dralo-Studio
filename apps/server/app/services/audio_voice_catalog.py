"""Account-scoped, read-only voice directory; never changes registered voices."""

# ruff: noqa: RUF001

import asyncio
from datetime import UTC, datetime
from urllib.parse import urlencode

from app.core.errors import ConflictError
from app.core.outbound_http import outbound_client
from app.core.provider_crypto import decrypt_api_key
from app.providers.protocols import effective_base_url, effective_protocol
from app.services.audio_verification_service import AudioCheckError, _json, check_fingerprint

KEY = "_audio_voice_catalog"
NAMES_KEY = "_audio_voice_names"
MAX_VOICES = 2000


def validate_names(defaults):
    names = defaults.get(NAMES_KEY, {})
    if (not isinstance(names, dict) or len(names) > 100
            or any(not isinstance(key, str) or not key.strip() or len(key) > 255
                   or not isinstance(value, str) or not value.strip() or len(value) > 200 for key, value in names.items())):
        raise ConflictError("音色名称最多 100 项，ID 最长 255 字符，名称最长 200 字符")


def read(provider, model):
    record = (model.default_params or {}).get(KEY)
    if not isinstance(record, dict) or record.get("fingerprint") != check_fingerprint(provider, model):
        return {"status": "unknown", "reason": "尚未同步当前渠道音色", "items": [], "synced_at": None, "partial": False}
    result = {key: value for key, value in record.items() if key != "fingerprint"}
    if effective_protocol(provider, model) == "stepfun_tts":
        result["items"] = [{**row, "category":row.get("category") or "system_voice"} for row in result.get("items", [])]
    return result


def registered(provider, model):
    names = (model.default_params or {}).get(NAMES_KEY, {})
    names = names if isinstance(names, dict) else {}
    catalog = {row["id"]: row for row in read(provider, model).get("items", [])}
    voices = (model.default_params or {}).get("voices", [])
    return [{"id": voice, "name": names.get(voice) or catalog.get(voice, {}).get("name") or voice}
            for voice in voices if isinstance(voice, str)] if isinstance(voices, list) else []


def selectable(provider, model):
    """Only the current account's StepFun catalog can extend registered IDs."""
    if effective_protocol(provider, model) != "stepfun_tts":
        return []
    record = read(provider, model)
    return record.get("items", []) if record.get("status") == "passed" else []


def _row(voice_id, name=None, language=None, category=None, description=None):
    if not isinstance(voice_id, str) or not voice_id.strip() or len(voice_id) > 255:
        raise AudioCheckError("音色目录包含无效 ID，原目录保留")
    return {"id": voice_id, "name": name[:200] if isinstance(name, str) and name else voice_id,
            "language": language[:80] if isinstance(language, str) else "",
            "category": category[:80] if isinstance(category, str) else "",
            "description": description[:1000] if isinstance(description, str) else ""}


async def _fetch(provider, model):
    protocol = effective_protocol(provider, model)
    if model.model_type != "tts":
        raise ConflictError("只有配音模型需要同步音色")
    if not provider.enabled or not model.enabled:
        raise ConflictError("请先启用渠道和模型")
    if protocol not in {"stepfun_tts", "minimax_audio_subscription", "elevenlabs_tts"}:
        return {"status": "unsupported", "reason": "此协议未接入音色目录，可手动填写音色 ID", "items": [], "partial": False}
    key = decrypt_api_key(provider.api_key_ciphertext)
    base = effective_base_url(provider, model).rstrip("/")
    headers = {"xi-api-key": key} if protocol == "elevenlabs_tts" else {"Authorization": "Bearer " + key}
    rows, partial = [], False
    async with outbound_client(timeout=min(provider.timeout_seconds, 30), proxy=provider.proxy_url, follow_redirects=False) as client:
        if protocol == "minimax_audio_subscription":
            data = await _json(client, "POST", base + "/get_voice", headers, {"voice_type": "all"})
            status = data.get("base_resp", {}) if isinstance(data, dict) else {}
            if not isinstance(status, dict) or type(status.get("status_code")) is not int or status["status_code"] != 0:
                raise AudioCheckError("音色目录返回业务错误，原目录保留")
            if not any(field in data for field in ("system_voice", "voice_cloning", "voice_generation")):
                raise AudioCheckError("音色目录格式无效，原目录保留")
            for field in ("system_voice", "voice_cloning", "voice_generation"):
                values = data.get(field, [])
                if not isinstance(values, list):
                    raise AudioCheckError("音色目录格式无效，原目录保留")
                for item in values:
                    if not isinstance(item, dict):
                        raise AudioCheckError("音色目录格式无效，原目录保留")
                    rows.append(_row(item.get("voice_id"), item.get("voice_name"), category=field))
        elif protocol == "stepfun_tts":
            data = await _json(client, "GET", base + "/audio/system_voices?" + urlencode({"model": model.model_id}), headers)
            voices = data.get("voices") if isinstance(data, dict) else None
            if not isinstance(voices, list):
                raise AudioCheckError("音色目录格式无效，原目录保留")
            details = data.get("voices-details", {})
            if not isinstance(details, dict):
                raise AudioCheckError("音色详情格式无效，原目录保留")
            for voice in voices:
                detail = details.get(voice, {}) if isinstance(voice, str) else {}
                if not isinstance(detail, dict):
                    raise AudioCheckError("音色详情格式无效，原目录保留")
                rows.append(_row(voice, detail.get("voice-name"), category="system_voice",
                                 description=detail.get("voice-description")))
        else:
            # Keep the configured proxy prefix, changing only the native API version.
            if not base.endswith("/v1"):
                raise AudioCheckError("ElevenLabs 音色同步需要以 /v1 结尾的接口地址", status="unsupported")
            cursor, seen = None, set()
            for _ in range(20):
                query = {"page_size": 100}
                if cursor:
                    query["next_page_token"] = cursor
                data = await _json(client, "GET", base[:-3] + "/v2/voices?" + urlencode(query), headers)
                voices = data.get("voices") if isinstance(data, dict) else None
                if not isinstance(voices, list) or type(data.get("has_more")) is not bool:
                    raise AudioCheckError("音色分页格式无效，原目录保留")
                for item in voices:
                    if not isinstance(item, dict):
                        raise AudioCheckError("音色目录格式无效，原目录保留")
                    labels = item.get("labels") or {}
                    rows.append(_row(item.get("voice_id"), item.get("name"),
                                     labels.get("language") if isinstance(labels, dict) else None, item.get("category")))
                if not data["has_more"]:
                    break
                cursor = data.get("next_page_token")
                if not isinstance(cursor, str) or not cursor or cursor in seen or len(cursor) > 2048:
                    raise AudioCheckError("音色分页游标无效，原目录保留")
                seen.add(cursor)
                if len(rows) >= MAX_VOICES:
                    partial = True
                    break
            else:
                partial = True
    unique = list({row["id"]: row for row in rows}.values())
    partial = partial or len(unique) > MAX_VOICES
    return {"status": "passed", "items": unique[:MAX_VOICES], "partial": partial,
            "reason": f"已同步 {min(len(unique), MAX_VOICES)} 个音色" + ("，目录未完整同步，其余音色可手动添加" if partial else "")}


async def synchronize(session, provider_id, model_id):
    from app.services.planning_capability_admin import lock_catalog

    provider, model = await lock_catalog(session, provider_id, model_id)
    try:
        async with asyncio.timeout(30):
            result = await _fetch(provider, model)
    except (AudioCheckError, TimeoutError) as exc:
        previous = read(provider, model)
        failure = exc.result if isinstance(exc, AudioCheckError) else {"status": "failed", "reason": "同步超时，原目录保留"}
        if failure.get("authentication_failed"):
            records = dict((model.default_params or {}).get("_audio_checks") or {})
            records["connection"] = {**failure, "fingerprint": check_fingerprint(provider, model), "checked_at": datetime.now(UTC).isoformat()}
            model.default_params = {**model.default_params, "_audio_checks": records}
            await session.flush()
        return {**previous, "last_sync": failure, "model_called": False}
    if result["status"] == "passed":
        records = dict((model.default_params or {}).get("_audio_checks") or {})
        for kind, record in list(records.items()):
            if isinstance(record, dict) and record.get("fingerprint") == check_fingerprint(provider, model) and record.get("authentication_failed"):
                records[kind] = {**record, "authentication_failed": False, "status": "unknown", "reason": "音色目录可访问，可重新检查"}
        model.default_params = {**model.default_params, "_audio_checks": records}
    model.default_params = {**(model.default_params or {}), KEY: {
        **result, "fingerprint": check_fingerprint(provider, model), "synced_at": datetime.now(UTC).isoformat()}}
    await session.flush()
    return {**read(provider, model), "model_called": False}
