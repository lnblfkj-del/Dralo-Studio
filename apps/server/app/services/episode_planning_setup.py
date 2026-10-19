"""Resolve planning defaults from the project and explicit channel specifications."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import EpisodeProduction, Project, ProviderModel
from app.services.episode_planning_capability import load_capability


async def planning_setup(session, episode, video_model_id, background_music=False, resolution=None, *, allow_unselected=False):
    capability = await load_capability(session, video_model_id)
    project = await session.get(Project, episode.project_id)
    ratio = (project.creation_settings or {}).get("aspect_ratio")
    if not ratio or ratio == "default":
        raise ConflictError("请先在项目设置中确定画幅比例")
    production = await session.scalar(select(EpisodeProduction).where(
        EpisodeProduction.episode_id == episode.id))
    model = await session.get(ProviderModel, video_model_id)
    preferred = (production.settings or {}).get("resolution") if production else None
    from app.services.video_configuration import request_defaults

    preferred = preferred or request_defaults(model).get("resolution")
    modes = [mode for mode in capability.modes if mode.aspect_ratio == ratio]
    if not modes:
        raise ConflictError(f"该视频模型不支持项目画幅 {ratio}，请选择其他模型")
    if background_music:
        modes = [mode for mode in modes if mode.native_audio and mode.bgm_control != "unsupported"]
    else:
        modes = [mode for mode in modes if not (mode.native_audio and mode.bgm_control == "unsupported")]
    if not modes:
        raise ConflictError("该视频模型不支持当前背景音乐设置，请调整配乐或更换模型")
    # Never infer first/last-frame intent from an image count. Those routes need
    # explicit frame roles at video submission, but can still plan without images.
    priority = {"multi_reference": 0, "text": 1, "single_image": 2,
                "first_frame": 3, "first_last_frame": 4}
    modes = [mode for mode in modes if mode.input_mode in priority]
    if not modes:
        raise ConflictError("该视频模型未配置可用的输入方式，请管理员补充模型设置")
    resolutions = sorted({mode.resolution for mode in modes})
    chosen = resolution if resolution is not None else preferred
    if not chosen and len(resolutions) == 1:
        chosen = resolutions[0]
    setup = {"aspect_ratio": ratio, "resolution": chosen if chosen in resolutions else "",
             "resolutions": resolutions, "requires_resolution_selection": chosen not in resolutions,
             "supports_background_music": any(m.bgm_control != "unsupported" for m in modes)}
    if chosen not in resolutions:
        if not allow_unselected:
            raise ConflictError("当前清晰度不受该模型支持，请选择清晰度；未自动降低清晰度")
        return capability, None, {**setup, "minimum_seconds": None, "maximum_seconds": None}
    mode = min((m for m in modes if m.resolution == chosen), key=lambda m: (priority[m.input_mode], m.key))
    rule = mode.durations
    return capability, mode, {
        **setup,
        "minimum_seconds": (min(rule.values_ms) if rule.values_ms else rule.minimum_ms) / 1000,
        "maximum_seconds": (max(rule.values_ms) if rule.values_ms else rule.maximum_ms) / 1000,
    }
