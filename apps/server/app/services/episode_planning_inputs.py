"""Read-only source validation before a paid planning request."""

from app.core.config import settings
from app.core.errors import ConflictError
from app.services.episode_planning_contract import fingerprint


def input_fingerprint(sources, assets, capability, mode_key, background_music, script_revision):
    from app.services.episode_planning_workflow import _digest

    return _digest({
        "sources": fingerprint(sources), "assets": assets,
        "capability": fingerprint(capability), "mode": mode_key,
        "background_music": background_music, "script_revision": script_revision,
    })


async def source_context(session, episode):
    from app.services.episode_planning_workflow import _source_context
    from app.services.screenplay_preflight import require_sources

    await require_sources(session, episode)
    sources, _ = await _source_context(session, episode)
    return {
        "script_revision": episode.script_revision,
        "source_fingerprint": fingerprint(sources),
        "sources": [unit.model_dump(mode="json") for unit in sources.units],
    }


async def preflight(
    session, episode, *, video_model_id, mode_key, background_music,
    expected_script_revision, expected_source_fingerprint, reference_bindings,
):
    from app.services.episode_planning_capability import load_capability
    from app.services.episode_planning_references import resolve_bindings
    from app.services.episode_planning_workflow import _digest, _source_context
    from app.services.episode_preparation_gate import require_preparation

    if not settings.episode_planning_epoch.strip():
        raise ConflictError("新规划执行命名空间尚未初始化")
    await require_preparation(session, episode)
    if episode.script_revision != expected_script_revision:
        raise ConflictError("正文版本已变化，请重新读取本集正文")
    sources, assets = await _source_context(session, episode)
    if fingerprint(sources) != expected_source_fingerprint:
        raise ConflictError("正文或角色解析结果已变化，请重新核对本集正文")
    capability = await load_capability(session, video_model_id)
    mode = next((item for item in capability.modes if item.key == mode_key), None)
    if mode is None:
        raise ConflictError("视频模型规格已变化，请重新选择模型")
    if background_music and mode.bgm_control == "unsupported":
        raise ConflictError("该视频模型不支持所选配乐设置")
    references = await resolve_bindings(session, episode, sources, mode, reference_bindings)
    assets = _digest({"assets": assets, "references": references})
    return {
        "fingerprint": input_fingerprint(
            sources, assets, capability, mode_key, background_music, episode.script_revision
        ),
        "reference_count": len(references), "model_called": False,
        "video_submission_ready": False,
    }
