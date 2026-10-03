"""Freeze Director V2 inputs and compile them against one video model."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select

from app.models import CanvasDocument, CanvasNode, Provider, ProviderModel
from app.providers.protocols import effective_protocol, is_toapis_model
from app.providers.video_contracts import VIDEO_CONTRACTS, validate_video_parameters


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _capability(model: ProviderModel, name: str, *aliases: str) -> bool:
    capabilities = set(model.capabilities or [])
    return name in capabilities or any(alias in capabilities for alias in aliases)


def _action(source: str, status: str, reason: str, *, count: int | None = None) -> dict[str, Any]:
    return {"source": source, "status": status, "reason": reason, **({"count": count} if count is not None else {})}


def _director_summary(package: dict[str, Any]) -> str:
    objects = package.get("objects", [])
    cameras = package.get("cameras", [])
    actions = [
        f"{item['name']}:{'/'.join(item.get('actions') or ['静态'])}"
        for item in objects
    ]
    return (
        f"导演镜头包：{package['duration_seconds']:g}秒，{package['fps']}fps，"
        f"画幅 {package['aspect_ratio']}；对象 {len(objects)} 个，机位 {len(cameras)} 个。"
        + (" 动作与路线：" + "；".join(actions) if actions else "")
        + " 白模预演仅作构图和动作参考，不要求逐帧复现。"
    )


async def _latest_preview_media(session, director_node: CanvasNode, revision: int) -> int | None:
    nodes = (
        await session.scalars(select(CanvasNode).where(CanvasNode.canvas_id == director_node.canvas_id))
    ).all()
    candidates = []
    for node in nodes:
        origin = (node.data or {}).get("director_origin") or {}
        media_id = (node.data or {}).get("media_id")
        if (
            node.node_type == "video"
            and origin.get("node_key") == director_node.node_key
            and origin.get("revision") == revision
            and type(media_id) is int
        ):
            candidates.append((node.id, media_id))
    return max(candidates, default=(0, None))[1]


async def freeze_director_package(
    session,
    project,
    director_context: dict[str, Any],
    references: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    document = director_context.get("director_document") or {}
    state = document.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("project"), dict):
        raise ValueError("导演台尚未保存有效工程")
    scene = state["project"]
    timeline = scene.get("timeline") or {"fps": 24, "durationFrames": 24, "tracks": []}
    if director_context.get("upstream"):
        active = next((camera for camera in scene.get("cameras", []) if camera.get("id") == scene.get("activeCameraId")), {})
        duration = float((active.get("motionPath") or {}).get("duration") or 5)
        timeline = {"fps":30, "durationFrames":round(duration * 30), "tracks":[]}
    fps = int(timeline.get("fps") or 24)
    frames = int(timeline.get("durationFrames") or fps)
    revision = int(document.get("revision") or 0)
    document = await session.scalar(
        select(CanvasDocument).where(CanvasDocument.project_id == project.id)
    )
    director_node = await session.scalar(
        select(CanvasNode).where(
            CanvasNode.canvas_id == document.id,
            CanvasNode.node_key == str(director_context["id"]),
        )
    ) if document else None
    preview_media_id = await _latest_preview_media(session, director_node, revision) if director_node else None
    objects = []
    for item in scene.get("objects", []):
        path = item.get("motionPath") or {}
        rig = item.get("characterRig") or {}
        actions = sorted(
            {
                value
                for value in [
                    rig.get("actionPresetId"),
                    *[key.get("actionPresetId") for key in path.get("keyframes", [])],
                    *[key.get("holdActionPresetId") for key in path.get("keyframes", [])],
                ]
                if isinstance(value, str) and value
            }
        )
        objects.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "asset_ref_id": item.get("assetRefId"),
                "actions": actions,
                "motion_path": path or None,
                "transform": item.get("transform"),
            }
        )
    package = {
        "schema_version": "director_shot_package.v1",
        "project_id": project.id,
        "director_node_key": str(director_context["id"]),
        "director_revision": revision,
        "director_state_fingerprint": _digest(state),
        "aspect_ratio": parameters.get("aspect_ratio") or state.get("viewportAspectRatio") or "16:9",
        "fps": fps,
        "duration_frames": frames,
        "duration_seconds": round(frames / fps, 4),
        "objects": objects,
        "cameras": scene.get("cameras", []),
        "timeline_tracks": timeline.get("tracks", []),
        "first_frame_media_id": next((r["media_id"] for r in references if r["role"] == "first_frame"), None),
        "last_frame_media_id": next((r["media_id"] for r in references if r["role"] == "last_frame"), None),
        "reference_media": [
            {key: ref.get(key) for key in ("media_id", "role", "node_id", "name", "kind", "purpose") if ref.get(key) is not None}
            for ref in references
        ],
        "preview_video_media_id": preview_media_id,
        "director_assets": [
            {key: asset.get(key) for key in ("id", "kind", "fileName", "url", "characterRigType", "characterImportReadiness") if asset.get(key) is not None}
            for asset in scene.get("assets", [])
        ],
    }
    package["package_fingerprint"] = _digest(package)
    return package


async def compile_video_submission(
    session,
    project,
    model: ProviderModel,
    provider: Provider,
    prompt: str,
    parameters: dict[str, Any],
    references: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    return await _compile_video_submission_a68(
        session, project, model, provider, prompt, parameters, references, contexts
    )


async def _compile_video_submission_a68(
    session,
    project,
    model: ProviderModel,
    provider: Provider,
    prompt: str,
    parameters: dict[str, Any],
    references: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.video_input_compiler import compile_video_input

    actions = [_action("prompt", "sent", "文字描述作为视频生成提示词发送", count=1)]
    blockers: list[str] = []
    director_contexts = [item for item in contexts if item.get("purpose") == "director_shot_package"]
    if len(director_contexts) > 1:
        blockers.append("一个视频节点只能连接一个导演镜头包")
    package = None
    if director_contexts:
        try:
            package = await freeze_director_package(session, project, director_contexts[0], references, parameters)
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(str(exc))
    raw_confirmations = parameters.get("video_input_confirmations", [])
    confirmations = raw_confirmations if isinstance(raw_confirmations, list) else []
    clean_parameters = {
        key: value for key, value in parameters.items()
        if key not in {"video_input_confirmations", "video_preflight_fingerprint"}
    }
    from app.services.style_generation_service import project_style_media
    style_media_id = None
    if session is not None and hasattr(project, "owner_id"):
        style_media_id = await project_style_media(session, project.id, project.owner_id, "video")
    compiled_references = list(references)
    if style_media_id and not any(item.get("media_id") == style_media_id for item in compiled_references):
        compiled_references.append({"media_id": style_media_id, "role": "reference_image",
                                    "purpose": "project_style"})
        actions.append(_action("project_style", "sent", "项目风格图纳入冻结的视频输入协议", count=1))
    compiled = compile_video_input(
        provider, model, compiled_references, clean_parameters,
        confirmed_downgrades=confirmations,
    )
    for field, choices in (("aspect_ratio", "aspect_ratios"), ("resolution", "resolutions"),
                           ("duration", "durations")):
        value, allowed = clean_parameters.get(field), (model.default_params or {}).get(choices)
        if value not in (None, "", "default", "模型默认") and isinstance(allowed, list) and value not in allowed:
            labels = {"aspect_ratio": "项目画幅", "resolution": "分辨率", "duration": "时长"}
            blockers.append(f"模型不支持{labels[field]} {value}")
    actions.extend(compiled["actions"])
    if effective_protocol(provider, model) == "dashscope_video_i2v" and compiled["input_mode"] != "text":
        actions.append(_action("aspect_ratio", "degraded", "百炼图生视频画幅跟随输入图片，不发送独立画幅参数"))
    blockers.extend(compiled["blockers"])
    effective_prompt = prompt.strip()
    if package:
        effective_prompt += "\n\n" + _director_summary(package)
        actions.append(_action(
            "director_shot_package",
            "sent" if _capability(model, "director_data", "camera_control") else "degraded",
            "导演数据随任务快照发送" if _capability(model, "director_data", "camera_control")
            else "模型无原生导演数据接口，降级为关键帧、路线和文字镜头描述",
        ))
    for item in contexts:
        if item.get("purpose") == "audio_track":
            actions.append(_action("audio_track", "ignored", "音轨仅用于后期合成，不发送给视频模型"))
    contract = {key: value for key, value in compiled.items() if key not in {
        "effective_references", "effective_parameters", "actions", "blockers", "ready", "required_confirmations"
    }}
    snapshot = {
        "model": {"provider_model_id": model.id, "model_id": model.model_id,
                  "capabilities": sorted(model.capabilities or []), "default_params": model.default_params or {}},
        "director_shot_package": package,
        "effective_references": compiled["effective_references"],
        "video_input_contract": contract,
        "required_confirmations": compiled["required_confirmations"],
        "actions": actions,
        "blockers": list(dict.fromkeys(blockers)),
        "effective_prompt": effective_prompt,
    }
    return {**snapshot, "ready": not snapshot["blockers"] and not snapshot["required_confirmations"],
            "submission_fingerprint": _digest(snapshot)}


async def _legacy_compile_video_submission(
    session,
    project,
    model: ProviderModel,
    provider: Provider,
    prompt: str,
    parameters: dict[str, Any],
    references: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    blockers: list[str] = []
    effective_refs: list[dict[str, Any]] = []
    director_contexts = [item for item in contexts if item.get("purpose") == "director_shot_package"]
    script_count = len([item for item in contexts if item.get("purpose") == "script"])
    actions.append(_action("prompt", "sent", "文字描述作为视频生成提示词发送", count=1 + script_count))
    if len(director_contexts) > 1:
        blockers.append("一个视频节点只能连接一个导演镜头包")
    package = None
    if director_contexts:
        try:
            package = await freeze_director_package(session, project, director_contexts[0], references, parameters)
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(str(exc))

    capabilities = set(model.capabilities or [])
    defaults = model.default_params or {}
    supports_refs = _capability(model, "reference_images", "multi_reference", "image_to_video")
    if is_toapis_model(provider, model) and model.model_id in {"wan2.6", "wan2.6-flash"}:
        from app.providers.toapis import model_defaults
        # Runtime contract only; never rewrite customer provider configuration.
        defaults = {**model_defaults(model.model_id), **defaults}
        supports_refs = True
    max_refs = defaults.get("max_reference_images", 4 if supports_refs else 0)
    max_refs = max_refs if type(max_refs) is int and max_refs >= 0 else 0
    adapter_accepts_images = effective_protocol(provider, model) in {"fake_video", *VIDEO_CONTRACTS} or is_toapis_model(provider, model)
    fake_adapter = effective_protocol(provider, model) == "fake_video"
    for role in ("first_frame", "last_frame", "reference_image"):
        items = [ref for ref in references if ref["role"] == role]
        if not items:
            continue
        if role == "first_frame":
            native = bool(defaults.get("supports_first_frame", fake_adapter or "first_frame" in capabilities or "first_last_frame" in capabilities))
        elif role == "last_frame":
            native = bool(defaults.get("supports_last_frame", fake_adapter or "last_frame" in capabilities or "first_last_frame" in capabilities))
        else:
            native = supports_refs
        if native and adapter_accepts_images:
            effective_refs.extend(items)
            actions.append(_action(role, "sent", "按模型原生图像条件发送", count=len(items)))
        elif role in {"first_frame", "last_frame"} and supports_refs and adapter_accepts_images:
            effective_refs.extend({**item, "role": "reference_image"} for item in items)
            actions.append(_action(role, "degraded", "模型不支持该帧位，降级为普通参考图", count=len(items)))
        else:
            reason = "当前模型未声明所需图像条件能力" if not native and not supports_refs else "当前渠道适配器尚未实现参考图上传"
            blockers.append(reason)
            actions.append(_action(role, "blocked", reason, count=len(items)))
    image_count = len([item for item in effective_refs if item["role"] == "reference_image"])
    if effective_protocol(provider, model) == "kling_video_i2v":
        actions.append(_action("aspect_ratio", "degraded", "可灵图生画幅跟随输入图片，不发送独立画幅参数；首尾帧能力需按模型启用"))
    if effective_protocol(provider, model).startswith("kling_video_") and len(prompt) > 2500:
        blockers.append("可灵提示词不能超过 2500 字符")
    if effective_protocol(provider, model) == "jimeng_video_first_last":
        actions.append(_action("aspect_ratio", "degraded", "即梦首尾帧画幅跟随首图，首尾图片必须同比例；不发送独立画幅参数"))
    if effective_protocol(provider, model) == "jimeng_video_pro" and effective_refs:
        actions.append(_action("aspect_ratio", "degraded", "即梦 Pro 图生按上游支持的最近画幅居中裁剪，不发送独立画幅参数；请检查首图构图"))
    if effective_protocol(provider, model).startswith("jimeng_video_"):
        if len(prompt) > 800:
            blockers.append("即梦提示词不能超过 800 字符")
    if effective_protocol(provider, model) == "dashscope_video_i2v" and effective_refs:
        actions.append(_action("aspect_ratio", "degraded", "百炼首图生视频以该图片作为首帧，画幅跟随输入图片；不发送独立画幅参数，需指定比例请先调整图片"))
    if is_toapis_model(provider, model) and model.model_id in {"wan2.6", "wan2.6-flash"} and effective_refs:
        actions.append(_action("aspect_ratio", "degraded", "Wan2.6 图生视频由输入图片决定画幅，不发送独立画幅参数；需要指定比例请先调整输入图片"))
    if image_count > max_refs:
        reason = f"参考图 {image_count} 张，超过模型上限 {max_refs} 张"
        blockers.append(reason)
        actions.append(_action("reference_image_limit", "blocked", reason, count=image_count))

    director_text = ""
    if package:
        director_text = _director_summary(package)
        if _capability(model, "director_data", "camera_control"):
            actions.append(_action("director_shot_package", "sent", "导演数据随任务快照发送；适配器按能力字段取用"))
        else:
            actions.append(_action("director_shot_package", "degraded", "模型无原生导演数据接口，降级为关键帧、路线和文字镜头描述"))
        if package.get("preview_video_media_id"):
            if _capability(model, "reference_video", "video_to_video"):
                actions.append(_action("preview_video", "degraded", "模型声明参考视频，但当前适配器未接入上传；降级为关键帧和镜头描述"))
            else:
                actions.append(_action("preview_video", "degraded", "模型不支持参考视频，降级为关键帧和镜头描述"))
    for item in contexts:
        if item.get("purpose") == "audio_track":
            actions.append(_action("audio_track", "ignored", "音轨仅用于 P8 后期合成，不发送给视频生成模型"))
    aspect = parameters.get("aspect_ratio")
    allowed_aspects = defaults.get("aspect_ratios")
    if aspect not in (None, "", "default", "模型默认") and isinstance(allowed_aspects, list) and aspect not in allowed_aspects:
        blockers.append(f"模型不支持项目画幅 {aspect}")
    resolution = parameters.get("resolution")
    allowed_resolutions = defaults.get("resolutions")
    if resolution not in (None, "", "default", "模型默认") and isinstance(allowed_resolutions, list) and resolution not in allowed_resolutions:
        blockers.append(f"模型不支持分辨率 {resolution}")
    duration = parameters.get("duration")
    allowed_durations = defaults.get("durations")
    if duration is not None and isinstance(allowed_durations, list) and float(duration) not in [float(value) for value in allowed_durations]:
        blockers.append(f"模型不支持 {float(duration):g} 秒时长")
    effective_prompt = prompt.strip()
    if effective_protocol(provider, model) in VIDEO_CONTRACTS:
        from app.core.errors import ConflictError
        try:
            validate_video_parameters(effective_protocol(provider, model), {**defaults, **parameters},
                              first=any(ref["role"] == "first_frame" for ref in effective_refs),
                              last=any(ref["role"] == "last_frame" for ref in references),
                              references=sum(ref["role"] == "reference_image" for ref in effective_refs))
            if sum(ref["role"] == "first_frame" for ref in effective_refs) > 1:
                raise ConflictError("当前视频协议只允许一张首帧图片")
        except ConflictError as exc:
            blockers.append(exc.message)
            actions.append(_action("channel_contract", "blocked", exc.message))
    # Preview and submission must agree with the actual channel adapter, not
    # merely with a model's (possibly broader) configured capability flags.
    if is_toapis_model(provider, model):
        from app.core.errors import ConflictError
        from app.providers.toapis import video_parameters

        adapter_parameters = {**defaults, **parameters}
        if isinstance(adapter_parameters.get("duration"), str):
            try:
                adapter_parameters["duration"] = int(adapter_parameters["duration"])
            except ValueError:
                pass
        try:
            for role in ("first_frame", "last_frame"):
                if sum(ref["role"] == role for ref in effective_refs) > 1:
                    raise ConflictError("首帧和尾帧各只能选择一个")
            video_parameters(model.model_id, adapter_parameters,
                             first=any(ref["role"] == "first_frame" for ref in effective_refs),
                             last=any(ref["role"] == "last_frame" for ref in effective_refs),
                             references=image_count)
        except ConflictError as exc:
            blockers.append(exc.message)
            actions.append(_action("channel_contract", "blocked", exc.message))
    if director_text:
        effective_prompt += "\n\n" + director_text
    snapshot = {
        "model": {"provider_model_id": model.id, "model_id": model.model_id, "capabilities": sorted(capabilities), "default_params": defaults},
        "director_shot_package": package,
        "effective_references": effective_refs,
        "actions": actions,
        "blockers": sorted(set(blockers)),
        "effective_prompt": effective_prompt,
    }
    return {
        **snapshot,
        "ready": not blockers,
        "submission_fingerprint": _digest(snapshot),
    }
