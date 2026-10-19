"""Explicit input-audio contract, separate from generated-video audio flags."""

import base64
import binascii
import math
from dataclasses import dataclass

from app.core.errors import ConflictError

ARK_REFERENCE_MODELS = frozenset({
    "doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-2-0-mini-260615",
})


def supports_reference_audio(protocol, model_id):
    return ((protocol == "ark_video_images" and model_id in ARK_REFERENCE_MODELS)
            or (protocol == "meaicc_video_images" and model_id == "sd-2-c4"))


def decode_reference_audio(value):
    try:
        prefix, encoded = value.split(",", 1)
        if prefix not in {"data:audio/mp3;base64", "data:audio/wav;base64"} or len(encoded) > 20_000_000:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        wav = "wav" in prefix
        valid = (raw[:4] == b"RIFF" and raw[8:12] == b"WAVE") if wav else (
            raw[:3] == b"ID3" or (len(raw) > 2 and raw[0] == 255 and raw[1] & 224 == 224))
        if not valid or not raw or len(raw) > 15_000_000:
            raise ValueError()
        return raw, "audio/wav" if wav else "audio/mpeg"
    except (AttributeError, ValueError, binascii.Error):
        raise ConflictError("参考音频必须为已核验的本地 MP3/WAV，单个不超过 15 MB") from None


@dataclass(frozen=True)
class VideoAudioReference:
    media_id: int
    content: str
    duration: float


def validate_reference_audio(protocol, model_id, audios, *, first, last, images, parameters):
    if not audios:
        return
    if not supports_reference_audio(protocol, model_id):
        raise ConflictError("当前视频接法尚未适配参考音频，不能静默忽略样本")
    if first or last or not images:
        raise ConflictError("参考音频目前须搭配普通参考图，不能与首尾帧模式混用")
    if parameters.get("generate_audio") is False:
        raise ConflictError("参考声线需要开启视频原生声音")
    if len(audios) > 3:
        raise ConflictError("本接法最多接收 3 段参考音频，请拆分片段或减少说话角色")
    total = 0
    for item in audios:
        if (not isinstance(item, VideoAudioReference) or type(item.media_id) is not int or item.media_id <= 0
                or type(item.duration) not in {int, float} or not math.isfinite(item.duration)
                or not 2 <= item.duration <= 15):
            raise ConflictError("每段参考音频须为 2–15 秒，不会自动裁剪")
        total += item.duration
        decode_reference_audio(item.content)
    # MEAICC documents the count, not Ark's shared 15-second audio budget.
    # Keep a local short-sample bound without applying another channel's limit.
    if protocol == "ark_video_images" and total > 15:
        raise ConflictError("参考音频合计不能超过 15 秒，不会自动删减角色样本")
