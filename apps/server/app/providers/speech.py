"""Opt-in speech contract; never infer TTS support from a model name."""

# ruff: noqa: RUF001
import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, GenerationFailedError


def speech_parameters(model, parameters, prompt):
    defaults = model.default_params
    if any(
        parameters.get(key) is not None
        for key in (
            "speed",
            "voice_id",
            "voice_reference",
            "reference_audio",
            "music",
            "instructions",
        )
    ):
        raise ConflictError("当前配音适配仅支持预设音色，不支持变速、参考音色、音乐生成或扩展指令")
    if "speech" not in model.capabilities or defaults.get("speech_verified") is not True:
        raise ConflictError("此模型的配音接口及预设音色尚未验证，未提交收费任务")
    voices = defaults.get("voices", [])
    voice = parameters.get("voice", defaults.get("voice"))
    if not isinstance(voices, list) or not isinstance(voice, str) or not voice or voice not in voices:
        raise ConflictError("请选择此模型已验证的预设音色；参考录音不能用于克隆")
    limit = defaults.get("max_input_chars", 4096)
    if type(limit) is not int or not 1 <= len(prompt.strip()) <= min(limit, 100000):
        raise ConflictError("配音文本为空或超过模型长度限制")
    return {"voice": voice, "response_format": "mp3"}


async def generate_speech(adapter, *, model, prompt, parameters):
    """Only the explicitly verified OpenAI-compatible MP3 speech contract."""
    try:
        async with (
            outbound_client(
                timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False
            ) as client,
            client.stream(
                "POST",
                adapter.base_url + "/audio/speech",
                headers={"Authorization": "Bearer " + adapter.api_key},
                json={
                    "model": model,
                    "input": prompt,
                    "voice": parameters["voice"],
                    "response_format": "mp3",
                },
            ) as response,
        ):
            if response.status_code != 200:
                raise GenerationFailedError(
                    f"配音渠道返回 HTTP {response.status_code}；请核对任务与账单后再提交"
                )
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise GenerationFailedError("配音结果超过 50 MB")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise GenerationFailedError("配音请求结果不明，已停止自动重发；请在渠道核对账单") from exc
    data = b"".join(chunks)
    if not (data.startswith(b"ID3") or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224)):
        raise GenerationFailedError("配音渠道未返回有效 MP3 文件")
    return {"audio_bytes": data}
