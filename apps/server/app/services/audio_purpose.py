"""Immutable output intent; old unclassified audio is never guessed from its text."""

# ruff: noqa: RUF001
from pathlib import Path

from app.core.errors import ConflictError

PURPOSES = {"voice_reference", "dialogue_audio", "music", "ambience", "sfx"}


def is_reference(media):
    return media.purpose == "voice_reference" or "voice-design" in Path(media.file_path).parts


def require_production_audio(media):
    if is_reference(media) or media.purpose == "model_test":
        raise ConflictError("参考声线或模型试听不能作为正式配音、配乐或音效加入成片")


def reference_parameters(model, protocol, parameters, description, *, provider=None):
    if not isinstance(description, str) or not description.strip() or len(description) > 1000:
        raise ConflictError("请填写不超过 1000 字符的声线描述")
    result = dict(parameters)
    if protocol == "stepfun_gen":
        if result.get("voice_description") not in (None, description.strip()):
            raise ConflictError("声线描述不一致，请重新确认")
        result["voice_description"] = description.strip()
        return result, "voice_design"
    if not isinstance(result.get("voice"), str) or not result["voice"].strip():
        raise ConflictError("请为此角色明确选择发音人，不会套用模型默认音色")
    if provider is not None:
        from app.services.audio_voice_catalog import selectable
        row = next((row for row in selectable(provider, model) if row["id"] == result["voice"]), {})
        metadata = row.get("name", "") + " " + row.get("description", "")
        male = "男" in metadata and "女" not in metadata
        female = "女" in metadata and "男" not in metadata
        if (("女声" in description and "男声" not in description and male)
                or ("男声" in description and "女声" not in description and female)):
            raise ConflictError("所选发音人的渠道性别描述与角色声线不符，请更换发音人")
    limit = {"stepaudio-3-tts": 500, "stepaudio-2.5-tts": 200}.get(model.model_id)
    method = "preset_sample"
    if protocol == "stepfun_tts" and limit:
        if len(description.strip()) > limit:
            raise ConflictError(f"当前模型的声线演绎描述最多 {limit} 字符，不会截断")
        if result.get("instruction") is not None and result["instruction"] != description.strip():
            raise ConflictError("声线描述与演绎指令冲突，请重新确认")
        result["instruction"] = description.strip()
        method = "directed_sample"
    elif result.get("instruction") is not None:
        raise ConflictError("当前参考样本模型不支持声线演绎指令")
    return result, method
