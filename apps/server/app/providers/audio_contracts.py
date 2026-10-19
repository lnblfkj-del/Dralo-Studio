"""Explicit audio contracts. Subscription entitlement is not protocol certification."""

# ruff: noqa: RUF001
import math

from app.core.errors import ConflictError

AUDIO_METADATA_KEYS = {"_audio_checks", "_audio_voice_catalog", "_audio_voice_names"}

AUDIO_PROTOCOLS = {
    "stepfun_gen": {"types": ["tts"], "version": "stepfun-gen-mp3-2026-10-19"},
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
        "stepfun_gen": {"stepaudio-3-gen-preview"},
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


def native_speech_parameters(model, parameters, prompt, protocol, *, provider=None):
    validate_audio_model(protocol, model.model_id)
    defaults = model.default_params or {}
    if model.model_type != "tts" or "speech" not in model.capabilities:
        raise ConflictError("请先设置模型的配音能力")
    if protocol == "stepfun_gen":
        description = parameters.get("voice_description")
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 498:
            raise ConflictError("请填写参考声线描述，最多 498 字符")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1000:
            raise ConflictError("试听文本需为 1–1000 字符")
        for field in ("voice", "voice_id", "voice_label", "reference_audio", "timestamp", "roles", "scripts", "task", "return_url", "stream_format", "instruction", "instructions"):
            if parameters.get(field) is not None or defaults.get(field) is not None:
                raise ConflictError(f"参考声线生成不接受 {field}，请使用声线描述与试听文本")
        if parameters.get("response_format", defaults.get("response_format", "mp3")) != "mp3":
            raise ConflictError("参考声线输出只支持 MP3")
        return {"voice_description": description.strip(), "response_format": "mp3"}
    voice = parameters.get("voice", defaults.get("voice"))
    voices = defaults.get("voices", [])
    if provider is not None and isinstance(voices, list):
        from app.services.audio_voice_catalog import selectable
        voices = [*voices, *(row["id"] for row in selectable(provider, model))]
    if not isinstance(voices, list) or not isinstance(voice, str) or not voice.strip() or voice not in voices or len(voice) > 255:
        raise ConflictError("请选择此模型已登记的音色 ID")
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
    if protocol == "stepfun_gen":
        if model.model_type != "tts" or "speech" not in model.capabilities or parameters.get("audio_usage") not in {"ambience", "sfx"}:
            raise ConflictError("请选择环境声或音效用途")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 998:
            raise ConflictError("环境声或音效描述需为 1–998 字符")
        if parameters.get("response_format", defaults.get("response_format", "mp3")) != "mp3":
            raise ConflictError("环境声或音效输出只支持 MP3")
        for field in ("voice", "voice_description", "instruction", "lyrics", "duration", "roles", "scripts", "return_url", "stream_format"):
            if parameters.get(field) is not None or defaults.get(field) is not None:
                raise ConflictError(f"环境声或音效生成不接受 {field}")
        return {"audio_usage": parameters["audio_usage"], "response_format": "mp3"}
    if protocol not in {"stepfun_music", "elevenlabs_music"} or model.model_type != "audio" or "music" not in model.capabilities:
        raise ConflictError("请先选择已接入的音乐协议并设置音乐能力")
    limit = defaults.get("max_input_chars", 4096)
    if type(limit) is not int or limit < 1 or not prompt.strip() or len(prompt) > min(limit, 4096):
        raise ConflictError("音乐描述为空或过长")
    for field in ("composition_plan", "seed", "song_audio", "vocal_audio", "reference_audio", "finetune_id", "task", "max_tokens", "temperature", "top_k", "top_p", "repetition_penalty"):
        if parameters.get(field) is not None or defaults.get(field) is not None:
            raise ConflictError(f"当前音乐协议未开放 {field}")
    instrumental = parameters.get("instrumental", defaults.get("instrumental", True))
    if type(instrumental) is not bool:
        raise ConflictError("instrumental 必须为布尔值")
    if parameters.get("force_instrumental", defaults.get("force_instrumental", True)) is not True:
        raise ConflictError("当前协议不接受 force_instrumental=false，请使用已接入的歌曲模式")
    lyrics = parameters.get("lyrics", defaults.get("lyrics"))
    if lyrics is not None and (not isinstance(lyrics, str) or len(lyrics) > 4096):
        raise ConflictError("歌词必须为文本，且不超过 4096 字符")
    if instrumental and lyrics is not None:
        raise ConflictError("纯音乐不接受歌词，请切换为歌曲")
    if parameters.get("response_format", defaults.get("response_format", "mp3")) != "mp3":
        raise ConflictError("音乐输出只支持 MP3")
    duration = parameters.get("duration", defaults.get("duration"))
    if protocol == "stepfun_music":
        if duration is not None or parameters.get("music_length_ms") is not None or defaults.get("music_length_ms") is not None:
            raise ConflictError("StepFun 音乐无法指定精确时长，不会发送无效时长参数")
        return {"instrumental": instrumental, "response_format": "mp3", **({"lyrics": lyrics.strip()} if lyrics and lyrics.strip() else {})}
    if parameters.get("music_length_ms") is not None or defaults.get("music_length_ms") is not None:
        raise ConflictError("请使用 duration（秒），避免两套时长单位冲突")
    seconds = _number(duration, 3, 600, "duration")
    milliseconds = seconds * 1000
    if milliseconds != int(milliseconds):
        raise ConflictError("音乐时长最多精确到毫秒")
    song_prompt = prompt if instrumental else "Generate a song with sung vocals. " + prompt + ("\nLyrics:\n" + lyrics if lyrics else "")
    if len(song_prompt) > 4100:
        raise ConflictError("音乐描述与歌词合计超过渠道限制，请缩短后再提交")
    return {"force_instrumental": instrumental, "music_length_ms": int(milliseconds), "music_prompt": song_prompt}
