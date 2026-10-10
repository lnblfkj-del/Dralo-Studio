"""Versioned production preferences; never mutate authored screenplay sources."""

from copy import deepcopy

from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models import Episode, EpisodeProduction

VERSION = "production_audio.v1"


def resolve(episode_settings=None):
    episode_settings = episode_settings or {}
    result = {"version": VERSION, "background_music": None, "voice_choice": "require_description",
              "source": "legacy"}
    value = episode_settings.get("background_music")
    if value is not None:
        if type(value) is not bool:
            raise ValidationError("背景音乐必须为开启或关闭")
        result.update(background_music=value, source="episode")
    choice = episode_settings.get("voice_choice")
    if choice is not None:
        if choice not in {"require_description", "model_choice"}:
            raise ValidationError("声线选择无效")
        result["voice_choice"] = choice
    return result


async def for_episode(session, project_id, episode_id):
    production = await session.scalar(select(EpisodeProduction).join(Episode, Episode.id == EpisodeProduction.episode_id).where(
        EpisodeProduction.episode_id == episode_id, Episode.project_id == project_id))
    return resolve(production.settings if production else {})


def instruction(policy):
    value = (policy or {}).get("background_music")
    if value is False:
        return ("本次关闭非剧情背景配乐：不要添加配乐BGM或情绪铺底。保留对白、旁白、环境声、音效；"
                "人物唱歌、收音机等剧情内音乐保留，并用‘剧情内音乐：’单独标注。不要用静音代替无配乐。")
    if value is None:
        return "每集必须包含明确的‘配乐BGM：’声音标注，描述原创配乐、对白下压低音量；留白场景明确无配乐。"
    return ("按剧情需要单独标注‘配乐BGM：’原创配乐的情绪、节奏、音色、进入与淡出，"
            "对白下压低音量；允许无配乐留白，不指定受版权保护曲目。环境声、音效与剧情内音乐分别标注。")


def effective_script(script, policy):
    """Derive submission audio, retaining omitted records and provenance in audit."""
    derived = deepcopy(script)
    omitted = []
    if (policy or {}).get("background_music") is False:
        if not isinstance(script, dict) or not script.get("camera"):
            raise ConflictError("关闭背景音乐需要完整片段脚本，请先补全结构化脚本；原提示词和视频未改变")
        music = []
        for item in (derived.get("audio") or {}).get("music", []):
            text = str(item.get("text") or "").strip()
            from app.services.screenplay_source_parser import AUDIO, split_cue, unwrap
            cue = split_cue(unwrap(text))
            kind = AUDIO.get(cue[0].casefold()) if cue else None
            if kind == "diegetic_music":
                music.append(item)
            elif kind == "music":
                omitted.append({**item, "reason": "background_music_disabled"})
            else:
                raise ConflictError("配乐记录未区分背景配乐与剧情内音乐，请在片段声音说明中明确标注")
        derived.setdefault("audio", {})["music"] = music
    return derived, omitted


def apply_prompt(prompt, script, policy):
    if (policy or {}).get("background_music") is None:
        return prompt, []
    from app.services.segment_script_semantics import compile_prompt
    derived, omitted = effective_script(script, policy)
    if not isinstance(derived, dict) or not derived.get("camera"):
        raise ConflictError("音频策略调整需要完整结构化片段脚本")
    if not isinstance(derived.get("scene"), dict) or any(not isinstance(derived.get(key), list) for key in ("performances", "dialogue")) or not isinstance(derived.get("audio"), dict) or not all(key in derived for key in ("entry_state", "exit_state")):
        raise ConflictError("片段脚本缺少场景、表演或声音结构，请先补全后调整音频策略")
    return compile_prompt(derived) + "\n" + instruction(policy), omitted


async def video_context(session, project, segment, provider, model, parameters):
    from app.services.video_model_contract import audio_contract
    policy = await for_episode(session, project.id, segment.episode_id)
    capability = audio_contract(provider, model)
    effective = dict(parameters)
    if policy["background_music"] is not None and capability["verified"] and capability["output"] == "optional":
        field = capability["parameter"]
        # A no-BGM preference must never become a total-audio mute.
        if effective.get(field) is False:
            raise ConflictError("当前关闭了全部声音；请先开启模型声音，背景音乐由独立选项控制")
        effective[field] = True
    effective["audio_policy"] = {**policy, "capability": capability}
    return effective
