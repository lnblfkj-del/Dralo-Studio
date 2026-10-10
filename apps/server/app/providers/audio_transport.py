"""Bounded native MP3 transports; no retries, redirects, or protocol fallback."""

# ruff: noqa: RUF001
import base64
import binascii
import json
from urllib.parse import quote

import httpx

from app.core.errors import GenerationFailedError, ProviderError
from app.core.outbound_http import outbound_client

MAX_AUDIO_BYTES = 50 * 1024 * 1024


def mp3_result(data, **metadata):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_AUDIO_BYTES:
        raise GenerationFailedError("音频结果为空或超过 50 MB")
    if not (data.startswith(b"ID3") or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224)):
        raise GenerationFailedError("渠道未返回 MP3 音频，不能将 JSON 错误或其他文件当作成功")
    return {"audio_bytes": data, **metadata}


class NativeAudioProvider:
    def __init__(self, *, protocol, base_url, api_key, timeout_seconds, proxy_url=None):
        self.protocol = protocol
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url

    async def discover_models(self):
        raise ProviderError("此原生音频协议请手动登记官方模型 ID；不会使用文本模型发现接口推定音频权限")

    async def _post(self, path, body, *, json_response=False, query=None):
        header = "xi-api-key" if self.protocol.startswith("elevenlabs_") else "Authorization"
        value = self.api_key if header == "xi-api-key" else "Bearer " + self.api_key
        bound = 2 * MAX_AUDIO_BYTES + 1024 * 1024 if json_response else MAX_AUDIO_BYTES
        try:
            async with (
                outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client,
                client.stream("POST", self.base_url + path, json=body, params=query,
                              headers={header: value}) as response,
            ):
                if response.status_code != 200:
                    raise GenerationFailedError(f"音频渠道返回 HTTP {response.status_code}；未自动重发，请核对额度和账单")
                size, chunks = 0, []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > bound:
                        raise GenerationFailedError("音频响应超过安全大小限制；已停止自动请求")
                    chunks.append(chunk)
                data = b"".join(chunks)
                request_id = response.headers.get("request-id", "")[:255]
        except httpx.HTTPError as exc:
            raise GenerationFailedError("音频请求结果不明；不会自动重发生成，请先核对渠道任务和账单") from exc
        if not json_response:
            return mp3_result(data, provider_request_id=request_id)
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError) as exc:
            raise GenerationFailedError("音频渠道返回无效 JSON；不会自动重新生成") from exc
        return payload

    async def generate_speech(self, *, model, prompt, parameters):
        voice = parameters["voice"]
        if self.protocol == "stepfun_tts":
            return await self._post("/audio/speech", {
                "model": model, "input": prompt, "voice": voice, "response_format": "mp3",
                **{key: parameters[key] for key in ("instruction", "speed", "volume") if key in parameters},
            })
        if self.protocol == "elevenlabs_tts":
            return await self._post("/text-to-speech/" + quote(voice, safe=""),
                                    {"model_id": model, "text": prompt},
                                    query={"output_format": "mp3_44100_128"})
        if self.protocol != "minimax_audio_subscription":
            raise ProviderError("当前原生音频协议不支持配音")
        payload = await self._post("/t2a_v2", {
            "model": model, "text": prompt, "stream": False, "output_format": "hex",
            "voice_setting": {"voice_id": voice, **{
                wire: parameters[key] for key, wire in (("speed", "speed"), ("volume", "vol"), ("pitch", "pitch")) if key in parameters}},
            "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000, "channel": 1},
        }, json_response=True)
        base = payload.get("base_resp")
        data = payload.get("data")
        if not isinstance(base, dict) or type(base.get("status_code")) is not int or base["status_code"] != 0:
            raise GenerationFailedError("MiniMax 配音返回业务错误；请核对订阅额度及模型权限，未自动重发")
        if not isinstance(data, dict) or type(data.get("status")) is not int or data["status"] != 2:
            raise GenerationFailedError("MiniMax 配音没有返回完成结果，不能标为成功")
        encoded = data.get("audio")
        if not isinstance(encoded, str) or len(encoded) > 2 * MAX_AUDIO_BYTES or len(encoded) % 2:
            raise GenerationFailedError("MiniMax 音频编码无效或过大")
        try:
            audio = binascii.unhexlify(encoded)
        except (ValueError, binascii.Error) as exc:
            raise GenerationFailedError("MiniMax 音频 Hex 编码损坏") from exc
        extra = payload.get("extra_info") or {}
        if not isinstance(extra, dict) or extra.get("audio_format", "mp3") != "mp3":
            raise GenerationFailedError("MiniMax 返回的音频格式与请求不符")
        metadata = {}
        if "usage_characters" in extra:
            count = extra["usage_characters"]
            metadata = ({"provider_usage_chars": count} if type(count) is int and 0 <= count <= 1_000_000_000
                        else {"provider_usage_invalid": True})
        return mp3_result(audio, provider_request_id=str(payload.get("trace_id") or "")[:255], **metadata)

    async def generate_music(self, *, model, prompt, parameters):
        if self.protocol != "elevenlabs_music":
            raise ProviderError("异步音乐必须先提交任务并保存任务 ID，不能当作同步音频调用")
        return await self._post("/music", {"model_id": model, "prompt": prompt,
            "force_instrumental": True, "music_length_ms": parameters["music_length_ms"]},
            query={"output_format": "mp3_44100_128"})

    async def submit_music(self, *, model, prompt, parameters):
        if self.protocol != "stepfun_music":
            raise ProviderError("当前协议没有异步音乐提交接口")
        payload = await self._post("/audio/music/submit", {
            "task": "text_to_music", "model_id": model, "caption": prompt,
            "instrumental": True, "response_format": "mp3",
        }, json_response=True)
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 255:
            raise GenerationFailedError("音乐提交未返回有效任务 ID；结果待核对，不自动重发")
        return {"task_id": task_id}

    async def query_music(self, task_id):
        if self.protocol != "stepfun_music":
            raise ProviderError("当前协议没有音乐查询接口")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 255:
            raise ProviderError("无效音乐任务 ID")
        payload = await self._post("/audio/music/query", {"task_id": task_id}, json_response=True)
        status = payload.get("status")
        if not isinstance(status, str):
            raise GenerationFailedError("音乐查询未返回有效业务状态")
        if status in {"PENDING", "RUNNING"}:
            return {"status": status, "task_id": task_id}
        if status == "FAILED":
            raise GenerationFailedError("渠道音乐任务已失败；保留任务 ID，重新生成可能再次收费", details={"audio_terminal_failure": True})
        if status != "SUCCESS" or payload.get("response_format") != "mp3":
            raise GenerationFailedError("音乐查询状态或输出格式无效")
        encoded = payload.get("audio")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_AUDIO_BYTES + 2) // 3):
            raise GenerationFailedError("音乐音频 Base64 无效或过大")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise GenerationFailedError("音乐音频 Base64 损坏") from exc
        return {"status": status, "task_id": task_id, **mp3_result(audio)}
