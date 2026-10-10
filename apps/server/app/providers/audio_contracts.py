"""Explicit audio contracts. Subscription entitlement is not protocol certification."""

# ruff: noqa: RUF001
import math

from app.core.errors import ConflictError

AUDIO_PROTOCOLS = {
    "stepfun_tts": {"types": ["tts"], "version": "stepfun-tts-mp3-2026-10-10"},
    "stepfun_music": {"types": ["audio"], "version": "stepfun-instrumental-2026-10-10"},
    "minimax_audio_subscription": {"types": ["tts"], "version": "minimax-t2a-v2-mp3-2026-10-10"},
    "elevenlabs_tts": {"types": ["tts"], "version": "elevenlabs-tts-mp3-2026-10-10"},
    "elevenlabs_music": {"types": ["audio"], "version": "elevenlabs-instrumental-2026-10-10"},
}
STEPFUN_TTS_MODELS = {"stepaudio-3-tts", "stepaudio-2.5-tts", "step-tts-2", "step-tts-mini"}
MINIMAX_TTS_MODELS = {
    f"speech-{version}-{quality}"
    for version in ("2.8", "2.6", "02", "01") for quality in ("hd", "turbo")
}
ELEVENLABS_TTS_LIMITS = {
    "eleven_v4": 10000, "eleven_v3": 5000, "eleven_multilingual_v2": 10000,
    "eleven_flash_v2_5": 40000, "eleven_flash_v2": 30000,
    "eleven_turbo_v2_5": 40000, "eleven_turbo_v2": 30000,
}


def validate_audio_model(protocol, model_id):
    allowed = {
        "stepfun_tts": STEPFUN_TTS_MODELS,
        "minimax_audio_subscription": MINIMAX_TTS_MODELS,
        "elevenlabs_tts": ELEVENLABS_TTS_LIMITS,
        "stepfun_music": {"stepaudio-3-music-preview"},
        "elevenlabs_music": {"music_v1", "music_v2", "music_v2_5"},
    }.get(protocol)
    if allowed is not None and model_id not in allowed:
        raise ConflictError("该音频协议尚未登记此模型的参数契约，未提交收费请求")


def _number(value, minimum, maximum, field):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not minimum <= value <= maximum):
        raise ConflictError(f"{field} 超出当前音频协议范围")
    return value


def native_speech_parameters(model, parameters, prompt, protocol):
    validate_audio_model(protocol, model.model_id)
    defaults = model.default_params or {}
    if "speech" not in model.capabilities or defaults.get("speech_verified") is not True:
        raise ConflictError("请先核验此模型及预设音色，未提交收费任务")
    voice = parameters.get("voice", defaults.get("voice"))
    voices = defaults.get("voices", [])
    if not isinstance(voices, list) or not isinstance(voice, str) or not voice.strip() or voice not in voices or len(voice) > 255:
        raise ConflictError("请选择此模型已核验的音色 ID")
    hard_limit = {"stepfun_tts": 1000, "minimax_audio_subscription": 9999}.get(
        protocol, ELEVENLABS_TTS_LIMITS.get(model.model_id, 4096))
    limit = defaults.get("max_input_chars", hard_limit)
    if type(limit) is not int or limit < 1 or not 1 <= len(prompt) <= min(limit, hard_limit) or not prompt.strip():
        raise ConflictError("配音文本为空或超过长度限制；不会截断台词，请按语义分段")
    for field in ("voice_id", "voice_reference", "reference_audio", "music", "voice_label",
                  "voice_settings", "voice_setting", "audio_setting", "return_url", "timestamp",
                  "stream_format", "pronunciation_map", "language_code", "language_boost",
                  "voice_modify", "timbre_weights", "emotion", "pronunciation_dict",
                  "sample_rate", "bitrate", "vol", "text_normalization", "apply_text_normalization"):
        if parameters.get(field) is not None or defaults.get(field) is not None:
            raise ConflictError(f"当前配音协议未开放 {field}，不会静默忽略")
    if parameters.get("response_format", defaults.get("response_format", "mp3")) != "mp3":
        raise ConflictError("当前配音链仅开放 MP3 输出")
    if parameters.get("output_format", defaults.get("output_format", "mp3_44100_128")) != "mp3_44100_128":
        raise ConflictError("当前配音链不支持其他输出格式或订阅等级专属编码")
    result = {"voice": voice, "response_format": "mp3"}
    instruction = parameters.get("instruction", defaults.get("instruction"))
    if parameters.get("instructions") is not None or defaults.get("instructions") is not None:
        raise ConflictError("请使用当前协议的 instruction 参数，不能套用其他模型的指令字段")
    if instruction is not None:
        instruction_limit = {"stepaudio-3-tts": 500, "stepaudio-2.5-tts": 200}.get(model.model_id)
        if protocol != "stepfun_tts" or instruction_limit is None or not isinstance(instruction, str) or len(instruction) > instruction_limit:
            raise ConflictError("当前模型不支持此演绎指令或指令过长")
        result["instruction"] = instruction
    # StepFun treats bracketed text as silent direction. Require review rather than drop speech.
    if protocol == "stepfun_tts" and model.model_id in {"stepaudio-3-tts", "stepaudio-2.5-tts"} and any(char in prompt for char in "()（）"):
        raise ConflictError("括号内容可能不会朗读，请先将演绎说明移到 instruction，需朗读的内容去掉括号")
    for field, bounds in {
        "speed": (0.5, 2),
        "volume": (0.1, 2) if protocol == "stepfun_tts" else (0.01, 10),
        "pitch": (-12, 12),
    }.items():
        value = parameters.get(field, defaults.get(field))
        if value is None:
            continue
        if protocol == "elevenlabs_tts" or (field == "pitch" and protocol != "minimax_audio_subscription"):
            raise ConflictError(f"当前配音契约未开放 {field}")
        if field == "pitch" and type(value) is not int:
            raise ConflictError("pitch 必须为整数")
        result[field] = _number(value, *bounds, field)
    return result


def music_parameters(model, parameters, prompt, protocol):
    validate_audio_model(protocol, model.model_id)
    defaults = model.default_params or {}
    if protocol not in {"stepfun_music", "elevenlabs_music"} or model.model_type != "audio" or "music" not in model.capabilities or defaults.get("music_verified") is not True:
        raise ConflictError("音乐模型及纯器乐接口尚未核验，未提交收费请求")
    limit = defaults.get("max_input_chars", 4096)
    if type(limit) is not int or limit < 1 or not prompt.strip() or len(prompt) > min(limit, 4096):
        raise ConflictError("音乐描述为空或过长")
    for field in ("lyrics", "composition_plan", "seed", "song_audio", "vocal_audio", "reference_audio", "finetune_id", "task", "max_tokens", "temperature", "top_k", "top_p", "repetition_penalty"):
        if parameters.get(field) is not None or defaults.get(field) is not None:
            raise ConflictError(f"纯器乐协议未开放 {field}")
    for field in ("instrumental", "force_instrumental"):
        if parameters.get(field, defaults.get(field, True)) is not True:
            raise ConflictError("本期音乐生成仅支持纯器乐")
    if parameters.get("response_format", defaults.get("response_format", "mp3")) != "mp3":
        raise ConflictError("音乐输出只支持 MP3")
    duration = parameters.get("duration", defaults.get("duration"))
    if protocol == "stepfun_music":
        if duration is not None or parameters.get("music_length_ms") is not None or defaults.get("music_length_ms") is not None:
            raise ConflictError("StepFun 音乐无法指定精确时长，不会发送无效时长参数")
        return {"instrumental": True, "response_format": "mp3"}
    if parameters.get("music_length_ms") is not None or defaults.get("music_length_ms") is not None:
        raise ConflictError("请使用 duration（秒），避免两套时长单位冲突")
    seconds = _number(duration, 3, 600, "duration")
    milliseconds = seconds * 1000
    if milliseconds != int(milliseconds):
        raise ConflictError("音乐时长最多精确到毫秒")
    return {"force_instrumental": True, "music_length_ms": int(milliseconds)}
