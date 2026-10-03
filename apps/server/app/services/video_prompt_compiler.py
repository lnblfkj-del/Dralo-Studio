"""Side-effect-free, versioned video prompt previews for the M3 model contracts.

This compiler does not submit jobs. M4 must freeze its output with the existing
video_input_contract and verify channel capabilities before using it for billing.
"""

from __future__ import annotations

import json
from hashlib import sha256
from itertools import pairwise
from math import isfinite
from typing import Any

from app.core.errors import ConflictError, ValidationError
from app.services.segment_script_semantics import compile_prompt

COMPILER_VERSION = "video_prompt_compiler.v6"
H3_SOURCE = "https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/SKILL.md"
SEEDANCE_SOURCE = "https://docs.volcengine.com/docs/ark/seedance-2-5-prompt-guide?lang=zh"
SEEDANCE_2_SOURCE = "https://www.volcengine.com/docs/82379/2222480?lang=zh"
KLING_SOURCE = "https://ir.kuaishou.com/node/11216/pdf"
INPUT_MODES = {"text", "first_frame", "first_last_frame", "single_image", "multi_reference"}
# No Kling/Seedance native multi-shot request field is implemented in current adapters.
NATIVE_MULTI_SHOT_FIELDS: dict[tuple[str, str], str] = {}
H3_REF_SECTIONS = (
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
)


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_video_prompt_certifications(value: Any) -> dict[str, Any]:
    """Validate admin evidence metadata; it is never a video request parameter."""
    if value == {}:
        return {}
    if not isinstance(value, dict) or set(value) != {"channel_revision", "endpoint_fingerprint", "modes"}:
        raise ValueError("视频提示词认证必须包含渠道版本、入口指纹和模式记录")
    revision, endpoint, modes = value["channel_revision"], value["endpoint_fingerprint"], value["modes"]
    if not isinstance(revision, str) or not revision.strip() or len(revision) > 128:
        raise ValueError("视频提示词渠道版本无效")
    if not isinstance(endpoint, str) or len(endpoint) != 64 or any(c not in "0123456789abcdef" for c in endpoint):
        raise ValueError("视频提示词入口指纹必须是 SHA-256 小写十六进制")
    if not isinstance(modes, dict) or not modes:
        raise ValueError("视频提示词认证至少需要一个输入模式")
    for mode, record in modes.items():
        if mode not in INPUT_MODES or not isinstance(record, dict):
            raise ValueError("视频提示词认证包含未知输入模式")
        if not {"status", "evidence", "revision"} <= set(record) or set(record) - {
            "status", "evidence", "revision", "native_multi_shot", "native_multi_shot_parameter",
            "production_enabled",
        }:
            raise ValueError("视频提示词认证记录字段无效")
        if record["status"] not in {"mock_verified", "channel_verified"}:
            raise ValueError("视频提示词认证状态无效")
        if any(not isinstance(record[key], str) or not record[key].strip() or len(record[key]) > 1000
               for key in ("evidence", "revision")):
            raise ValueError("视频提示词认证缺少证据或修订号")
        if "native_multi_shot" in record and type(record["native_multi_shot"]) is not bool:
            raise ValueError("原生多镜头认证必须为布尔值")
        if "production_enabled" in record and type(record["production_enabled"]) is not bool:
            raise ValueError("生产提示词启用状态必须为布尔值")
        if record.get("production_enabled") and record["status"] != "channel_verified":
            raise ValueError("生产提示词仅能在渠道认证后启用")
        if "native_multi_shot_parameter" in record and (
            not isinstance(record["native_multi_shot_parameter"], str)
            or len(record["native_multi_shot_parameter"]) > 128
        ):
            raise ValueError("原生多镜头参数名无效")
    return value


def video_prompt_endpoint_fingerprint(provider: Any, model: Any) -> str:
    from app.providers.protocols import effective_base_url, effective_protocol, is_toapis_model
    from app.providers.video_contracts import VIDEO_CONTRACTS

    protocol = effective_protocol(provider, model)
    protocol_version = VIDEO_CONTRACTS.get(protocol)
    if protocol_version is None and is_toapis_model(provider, model):
        protocol_version = "toapis.video.v1"
    return _hash([provider.id, model.id, protocol, protocol_version,
                  effective_base_url(provider, model), model.model_id])


def resolve_model_prompt_profile(provider: Any, model: Any, input_mode: str) -> dict[str, Any]:
    from app.providers.protocols import effective_protocol

    config = validate_video_prompt_certifications(getattr(model, "video_prompt_certifications", None) or {})
    endpoint = video_prompt_endpoint_fingerprint(provider, model)
    channel_revision = config.get("channel_revision") or "unverified"
    route = {"protocol": effective_protocol(provider, model), "channel_revision": channel_revision,
             "model_id": model.model_id, "input_mode": input_mode}
    record = (config.get("modes") or {}).get(input_mode) if config.get("endpoint_fingerprint") == endpoint else None
    proof = {"route": route, **record} if record else None
    resolved = resolve_prompt_profile(**route, certification=proof)
    return {**resolved, "endpoint_fingerprint": endpoint,
            "certification_stale": bool(config and config["endpoint_fingerprint"] != endpoint),
            "local_adapter_preflight": _probe_adapter_mode(provider, model, input_mode, resolved["recipe"])}


def _recipe(protocol: str, model_id: str, mode: str) -> tuple[str, str | None]:
    lower = model_id.lower()
    if lower in {"h3", "h3-max", "minimax-h3", "minimax-h3-max"}:
        return ("h3_ref2va" if mode in {"single_image", "multi_reference"} else "h3_base", H3_SOURCE)
    if "seedance-2-5" in lower or "seedance-2.5" in lower:
        return "seedance_2_5", SEEDANCE_SOURCE
    if "seedance-2-0" in lower or "seedance-2.0" in lower:
        return "seedance_2_0", SEEDANCE_2_SOURCE
    if lower.startswith("kling-"):
        return "kling", KLING_SOURCE
    return "generic", None


def resolve_prompt_profile(
    *, protocol: str, channel_revision: str, model_id: str, input_mode: str,
    certification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve an exact route; a product guide never certifies a customer channel."""
    if not all(_text(value) for value in (protocol, channel_revision, model_id)):
        raise ValidationError("提示词档案缺少协议、渠道版本或模型 ID")
    if input_mode not in INPUT_MODES:
        raise ValidationError("视频输入模式未登记，不能编译提示词")
    recipe, source = _recipe(protocol, model_id, input_mode)
    route = {
        "protocol": protocol, "channel_revision": channel_revision,
        "model_id": model_id, "input_mode": input_mode,
    }
    proof = certification if isinstance(certification, dict) else {}
    evidence_matches = (
        proof.get("route") == route
        and proof.get("status") in {"mock_verified", "channel_verified"}
        and bool(_text(proof.get("evidence")))
        and bool(_text(proof.get("revision")))
    )
    certified = evidence_matches and proof["status"] == "channel_verified"
    return {
        **route, "recipe": recipe, "source_url": source,
        "verification": ("channel_verified" if certified else "mock_verified" if evidence_matches
                         else "documented_only" if source else "generic"),
        "evidence": _text(proof.get("evidence")) if evidence_matches else None,
        "certification_revision": _text(proof.get("revision")) if evidence_matches else None,
        "production_enabled": bool(certified and proof.get("production_enabled") is True),
        "native_multi_shot": bool(certified and proof.get("native_multi_shot") is True
                                  and _text(proof.get("native_multi_shot_parameter"))
                                  and NATIVE_MULTI_SHOT_FIELDS.get((protocol, model_id))
                                  == proof.get("native_multi_shot_parameter")),
        "native_multi_shot_parameter": _text(proof.get("native_multi_shot_parameter")) if certified else None,
        "local_adapter_preflight": {"passed": False, "reason": "尚未按当前模型路由执行本地适配器预检"},
    }


def _probe_adapter_mode(provider: Any, model: Any, mode: str, recipe: str) -> dict[str, Any]:
    """Exercise the registered adapter's local parameter contract; no media or HTTP."""
    from app.providers.protocols import effective_protocol, is_toapis_model
    from app.providers.toapis import VIDEO_MODELS
    from app.providers.video_contracts import VIDEO_CONTRACTS
    from app.services.video_input_compiler import compile_video_input

    protocol = effective_protocol(provider, model)
    toapis = is_toapis_model(provider, model)
    if recipe.startswith("h3_") and protocol != "minimax_video_v2":
        return {"passed": False, "reason": "H3 模型与当前官方 V2 视频协议不匹配"}
    if recipe.startswith("seedance_") and not (
        protocol in {"ark_video_t2v", "ark_video_images"}
        or toapis and model.model_id == "seedance-2-5"
    ):
        return {"passed": False, "reason": "Seedance 模型与当前视频协议不匹配"}
    if recipe == "kling" and not (
        protocol.startswith("kling_video_")
        or toapis and model.model_id in {"kling-v2-6", "kling-v3-omni"}
    ):
        return {"passed": False, "reason": "可灵模型与当前视频协议不匹配"}
    if protocol not in VIDEO_CONTRACTS and not (toapis and model.model_id in VIDEO_MODELS):
        return {"passed": False, "reason": "当前视频协议没有已注册的模型适配器"}
    declared = (model.default_params or {}).get("supported_video_input_modes")
    if isinstance(declared, list) and declared and mode not in declared:
        return {"passed": False, "reason": "当前模型配置未声明此视频输入模式"}
    refs_by_mode = {
        "text": [], "first_frame": [{"media_id": 1, "role": "first_frame"}],
        "first_last_frame": [{"media_id": 1, "role": "first_frame"}, {"media_id": 2, "role": "last_frame"}],
        "single_image": [{"media_id": 1, "role": "reference_image"}],
        "multi_reference": [{"media_id": 1, "role": "reference_image"},
                            {"media_id": 2, "role": "reference_image"}],
    }
    durations = (model.default_params or {}).get("durations") or []
    duration = (model.default_params or {}).get("duration") or (durations[0] if durations else 5)
    try:
        compiled = compile_video_input(provider, model, refs_by_mode[mode], {"duration": duration},
                                       preview_only=recipe.startswith("h3_"))
    except (ConflictError, ValueError, TypeError) as exc:
        return {"passed": False, "reason": str(exc)}
    if not compiled["ready"]:
        reason = (compiled["blockers"] or ["本地参数合同未通过"])[0]
        return {"passed": False, "reason": reason}
    return {"passed": True, "reason": (
        "H3 本地参数合同仅供离线预览；生产适配器仍未开放"
        if recipe.startswith("h3_") else "本地参数合同通过；未验证渠道真实接收或生成质量"
    )}


def _shot_lines(script: dict[str, Any], *, timeline: bool) -> list[str]:
    shots = script.get("camera") or []
    performances = script.get("performances") or []
    dialogues = script.get("dialogue") or []
    audio = script.get("audio") or {}
    lines, start = [], 0.0
    for index, shot in enumerate(shots, 1):
        duration = shot.get("duration")
        if type(duration) not in (int, float) or not isfinite(duration) or duration <= 0:
            raise ValidationError("镜头时长缺失，不能编译模型时序")
        shot_id = shot.get("shot_id")
        if timeline:
            heading = f"[{start:g}s-{start + duration:g}s] 镜头{index}"
        else:
            heading = f"镜头{index}（{duration:g}秒）"
        camera = "、".join(filter(None, (_text(shot.get("shot_size")), _text(shot.get("camera_angle")),
                                          _text(shot.get("camera_movement")))))
        parts = [f"{heading}：{camera}" if camera else f"{heading}："]
        parts.extend(
            "动作：" + "，".join(filter(None, (_text(item.get("subject")), _text(item.get("action")),
                                           _text(item.get("expression")))))
            for item in performances if item.get("shot_id") == shot_id
        )
        parts.extend(
            f"台词：{_text(item.get('speaker'))}（{_text(item.get('tone'))}）：{_text(item.get('text'))}"
            for item in dialogues if item.get("shot_id") == shot_id
        )
        for kind, label in (("ambience", "环境声"), ("sound_effects", "音效"), ("music", "配乐")):
            parts.extend(f"{label}：{_text(item.get('text'))}" for item in audio.get(kind, [])
                         if item.get("shot_id") == shot_id)
        lines.append("；".join(parts))
        start += float(duration)
    return lines


def _kling_single_shot_prompt(
    script: dict[str, Any], *, context: str, project_style: str,
    voice_guidance: str, refs: list[dict[str, Any]], model_id: str,
    input_mode: str,
) -> str:
    shot = script["camera"][0]
    shot_id = shot["shot_id"]
    duration = shot["duration"]
    lines = []
    if _text(project_style):
        lines.append(_text(project_style))
    if context:
        lines.append(context.replace(" / ", "，"))
    if model_id == "kling-v3-omni" and refs:
        labels = [f"<<<image_{index}>>>" for index in range(1, len(refs) + 1)]
        if input_mode == "first_frame":
            lines.append(f"以{labels[0]}为起始画面")
        elif input_mode == "first_last_frame":
            lines.append(f"以{labels[0]}为起始画面，{labels[1]}为结束画面")
        else:
            lines.append(f"参考{'、'.join(labels)}中已绑定人物、物件和场景的外观")
    entry = _text(script.get("entry_state"))
    if entry:
        lines.append(f"开场即进入动作：{entry}")
    for performance in script.get("performances") or []:
        if performance.get("shot_id") != shot_id:
            continue
        subject = _text(performance.get("subject"))
        action = _text(performance.get("action"))
        expression = _text(performance.get("expression"))
        if action:
            lines.append("，".join(part for part in (subject, action, expression) if part))
    framing = "、".join(filter(None, (
        _text(shot.get("shot_size")), _text(shot.get("camera_angle")),
        _text(shot.get("camera_movement")),
    )))
    lines.append(f"{duration:g}秒单镜头，{framing}，动作连续并清楚拍到结果" if framing
                 else f"{duration:g}秒单镜头，动作连续并清楚拍到结果")
    exit_state = _text(script.get("exit_state"))
    if exit_state:
        lines.append(f"结尾：{exit_state}")
    for dialogue in script.get("dialogue") or []:
        if dialogue.get("shot_id") == shot_id and _text(dialogue.get("text")):
            lines.append(
                f"{_text(dialogue.get('speaker'))}"
                f"（{_text(dialogue.get('tone'))}）说：“{_text(dialogue.get('text'))}”"
            )
    for kind, label in (("ambience", "环境声"), ("sound_effects", "音效"), ("music", "配乐")):
        for item in (script.get("audio") or {}).get(kind, []):
            if item.get("shot_id") == shot_id and _text(item.get("text")):
                lines.append(f"{label}：{_text(item.get('text'))}")
    if _text(voice_guidance):
        lines.append(f"声音要求：{_text(voice_guidance)}")
    return "。".join(line.rstrip("。") for line in lines if line) + "。"


def _validate_input_and_script(script: dict[str, Any], input_contract: dict[str, Any]) -> list[dict[str, Any]]:
    if input_contract.get("schema_version") != "video_input_contract.v2":
        raise ValidationError("提示词编译需要当前视频输入合同 v2")
    refs = input_contract.get("effective_references")
    if not isinstance(refs, list):
        raise ValidationError("视频输入引用快照无效")
    roles: dict[str, list[int]] = {"first_frame": [], "last_frame": [], "reference_image": []}
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("role") not in roles:
            raise ValidationError("视频输入存在未知素材用途")
        media_id = ref.get("media_id")
        if type(media_id) is not int or media_id <= 0:
            raise ValidationError("视频输入素材缺少有效媒体版本")
        roles[ref["role"]].append(media_id)
    if len(set(roles["first_frame"])) > 1 or len(set(roles["last_frame"])) > 1:
        raise ValidationError("首尾帧各只能采用一个媒体版本")
    first, last, ordinary = (bool(roles[key]) for key in ("first_frame", "last_frame", "reference_image"))
    expected_mode = (
        "first_last_frame" if first and last else "first_frame" if first or last
        else "multi_reference" if len(set(roles["reference_image"])) > 1
        else "single_image" if ordinary else "text"
    )
    if expected_mode != input_contract.get("input_mode") or ((first or last) and ordinary):
        raise ValidationError("视频输入模式与实际采用素材用途冲突，请重新预检")
    if not isinstance(script.get("camera"), list) or not script["camera"]:
        raise ValidationError("片段缺少镜头语义")
    ids = [shot.get("shot_id") for shot in script["camera"] if isinstance(shot, dict)]
    if len(ids) != len(script["camera"]) or any(type(shot_id) is not int or shot_id <= 0 for shot_id in ids):
        raise ValidationError("镜头语义包含无效分镜编号")
    if len(set(ids)) != len(ids):
        raise ValidationError("镜头语义包含重复分镜编号")
    durations = [shot.get("duration") for shot in script["camera"]]
    if any(type(duration) not in (int, float) or not isfinite(duration) or duration <= 0 for duration in durations):
        raise ValidationError("镜头时长必须是有限正数")
    generation_duration = (input_contract.get("effective_parameters") or {}).get("duration")
    if type(generation_duration) not in (int, float) or not isfinite(generation_duration) or generation_duration <= 0:
        raise ValidationError("视频输入缺少有效生成时长")
    if sum(durations) > generation_duration + 0.05:
        raise ValidationError("片段镜头时间轴超过本次模型生成时长")
    return refs


def _reference_labels(refs: list[dict[str, Any]], protocol: str, recipe: str) -> list[dict[str, Any]]:
    if recipe not in {"h3_base", "h3_ref2va", "seedance_2_5", "seedance_2_0"}:
        return []
    if protocol in {"ark_video_images", "ark_video_t2v"} or recipe == "h3_base":
        order = {"first_frame": 0, "last_frame": 1, "reference_image": 2}
        ordered = sorted(enumerate(refs), key=lambda pair: (order[pair[1]["role"]], pair[0]))
        status = "adapter_order_verified" if protocol in {"ark_video_images", "ark_video_t2v"} else "preview_only"
    else:
        ordered = list(enumerate(refs))
        status = "preview_only"
    prefix = "<Picture " if recipe.startswith("h3_") else "图片"
    return [
        {"label": f"{prefix}{index}>" if recipe.startswith("h3_") else f"{prefix}{index}",
         "media_id": ref["media_id"], "role": ref["role"], "purpose": ref.get("purpose"),
         "order_status": status}
        for index, (_, ref) in enumerate(ordered, 1)
    ]


def _seedance_reference_guidance(labels: list[dict[str, Any]]) -> list[str]:
    if not labels or any(item["order_status"] != "adapter_order_verified" for item in labels):
        return []
    lines = []
    for item in labels:
        role = item["role"]
        if role == "first_frame":
            use = "作为首帧构图与主体状态"
        elif role == "last_frame":
            use = "作为结尾画面状态"
        else:
            purpose = _text(item.get("purpose"))
            use = f"仅参考{purpose}" if purpose else "作为画面参考, 具体用途待人工核对"
        lines.append(f"{item['label']}{use}")
    return ["参考素材映射（按实际上传顺序）：" + "；".join(lines)]


def _h3_frame_instruction(labels: list[dict[str, Any]], duration: float, shot_count: int) -> str:
    first = next((item["label"] for item in labels if item["role"] == "first_frame"), None)
    last = next((item["label"] for item in labels if item["role"] == "last_frame"), None)
    if first and last:
        return ("How the reference pictures align with the target video — "
                f"Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
                f"Picture 2 (from Shot {shot_count}) aligns with the {duration:.2f}-second mark of the target video.")
    if first:
        return f"For the target video, at 0.00 seconds into the target video, {first} (from [Shot 1]) is fully referenced."
    if last:
        return ("How the reference pictures align with the target video — "
                f"{last} (from [Shot {shot_count}]) aligns with the {duration:.2f}-second mark of the target video.")
    return ""


def compile_model_prompt(
    script: dict[str, Any], *, profile: dict[str, Any], input_contract: dict[str, Any],
    project_style: str = "", voice_guidance: str = "", require_submission: bool = False,
) -> dict[str, Any]:
    """Compile from saved semantics and actual frozen references, never inferred assets."""
    mode = input_contract.get("input_mode")
    if mode != profile.get("input_mode"):
        raise ValidationError("素材输入模式已变化，请按当前模式重新编译")
    if input_contract.get("protocol") != profile.get("protocol") or input_contract.get("model_id") != profile.get("model_id"):
        raise ValidationError("视频输入协议或模型已变化，请重新预检并编译")
    refs = _validate_input_and_script(script, input_contract)
    recipe = profile["recipe"]
    blockers = list(input_contract.get("blockers") or [])
    if input_contract.get("required_confirmations"):
        blockers.append("视频输入仍有待确认的素材用途降级，不能自动提交")
    if input_contract.get("ready") is False and not blockers:
        blockers.append("视频输入预检未通过，请重新核对素材与参数")
    if require_submission and recipe != "generic" and profile["verification"] != "channel_verified":
        blockers.append("当前协议、渠道版本、模型和模式尚未真实认证；产品文档不能代替渠道验收")
    if require_submission and not (profile.get("local_adapter_preflight") or {}).get("passed"):
        blockers.append((profile.get("local_adapter_preflight") or {}).get("reason") or "本地视频适配器预检未通过")
    if require_submission and recipe.startswith("h3_"):
        blockers.append("MiniMax H3 视频提交适配器尚未接入")
    if require_submission and recipe == "h3_ref2va":
        blockers.append("H3 Ref2VA 英文六段内容及素材标签尚需经官方格式校验")
    shots = script["camera"]
    reference_labels = _reference_labels(refs, profile["protocol"], recipe)
    if require_submission and recipe.startswith("seedance_") and refs:
        blockers.append(
            "Seedance 参考素材顺序已本地映射, 仍需真实渠道验证参考效果"
            if all(item["order_status"] == "adapter_order_verified" for item in reference_labels)
            else "Seedance 参考素材与提示词标签尚未完成当前渠道的逐项映射"
        )
    split = len(shots) > 1 and not profile["native_multi_shot"]
    if require_submission and split:
        blockers.append("当前渠道未认证原生多镜头；须先拆成逐镜头任务并完成费用/衔接预检")
    if require_submission:
        blockers.append("M3 提示词编译仅供预览；生产任务冻结和提交由 M4 接入后再开放")
    scene = script.get("scene") or {}
    context = " / ".join(filter(None, (_text(scene.get("name")), _text(scene.get("location")),
                                     _text(scene.get("time_of_day")))))
    intro = [f"场景：{context}"] if context else []
    if _text(project_style):
        intro.append(f"项目风格：{_text(project_style)}")
    if _text(voice_guidance):
        intro.append(f"声音要求：{_text(voice_guidance)}")
    if recipe == "generic":
        prompt = compile_prompt(script)
        extras = [line for line in intro if not line.startswith("场景：")]
        prompt = "\n".join([prompt, *extras])
    elif recipe.startswith("h3_"):
        sections = H3_REF_SECTIONS if recipe == "h3_ref2va" else (
            "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music",
        )
        body = "\n".join([*intro, *_shot_lines(script, timeline=False)])
        sections_preview = "\n\n".join(
            f"{name}:\n{body if index == 0 else '[requires authored English rewrite]'}"
            for index, name in enumerate(sections)
        )
        alignment = _h3_frame_instruction(reference_labels, input_contract["effective_parameters"]["duration"],
                                          len(shots)) if recipe == "h3_base" else ""
        prompt = "\n\n".join(filter(None, (alignment, sections_preview)))
    elif (
        recipe == "kling"
        and len(shots) == 1
        and input_contract["effective_parameters"].get("audio") is not True
    ):
        prompt = _kling_single_shot_prompt(
            script, context=context, project_style=project_style,
            voice_guidance=voice_guidance, refs=refs,
            model_id=profile["model_id"], input_mode=mode,
        )
    else:
        integer_timeline = recipe == "seedance_2_5" and all(
            type(shot.get("duration")) in (int, float) and float(shot["duration"]).is_integer()
            for shot in shots
        )
        lines = _shot_lines(script, timeline=integer_timeline)
        if recipe == "seedance_2_0":
            lines = [f"分镜 {index}：{line}" for index, line in enumerate(lines, 1)]
        reference_guidance = _seedance_reference_guidance(reference_labels) if recipe.startswith("seedance_") else []
        prompt = "\n".join([*intro, *reference_guidance, *lines,
                            f"结束状态：{_text(script.get('exit_state'))}"])
    reference_manifest = [
        {key: ref.get(key) for key in ("role", "media_id", "asset_id", "asset_version_id", "purpose") if key in ref}
        for ref in refs
    ]
    shot_units = []
    if split:
        for index, shot in enumerate(shots):
            shot_id = shot.get("shot_id")
            shot_units.append({
                "shot_id": shot_id, "duration": shot["duration"], "camera": shot,
                "performances": [item for item in script.get("performances", []) if item.get("shot_id") == shot_id],
                "dialogue": [item for item in script.get("dialogue", []) if item.get("shot_id") == shot_id],
                "audio": {key: [item for item in values if item.get("shot_id") == shot_id]
                          for key, values in (script.get("audio") or {}).items()},
                "entry_state": shot.get("entry_state") or (script.get("entry_state") if index == 0 else None),
                "exit_state": shot.get("exit_state") or (script.get("exit_state") if index == len(shots) - 1 else None),
                "requires_continuity_review": True,
            })
    compiled = {
        "compiler_version": COMPILER_VERSION,
        "profile": profile,
        "prompt": prompt,
        "reference_manifest": reference_manifest,
        "reference_labels": reference_labels,
        "parameters": dict(input_contract.get("effective_parameters") or {}),
        "submission_plan": "split_single_shot" if split else "single_request",
        "shot_units": shot_units,
        "continuity_edges": [
            {"from_shot_id": left["shot_id"], "to_shot_id": right["shot_id"], "status": "unresolved"}
            for left, right in pairwise(shot_units)
        ],
        "ready_for_submission": False,
        "warnings": (["H3 格式仅为结构预览；尚未翻译为官方要求的英文内容"]
                     if recipe.startswith("h3_") else
                     ["镜头含非整数秒时长，Seedance 2.5 改用分镜序号，未伪造整数秒时间轴"]
                     if recipe == "seedance_2_5" and not integer_timeline else []),
        "blockers": blockers,
    }
    compiled["fingerprint"] = _hash([COMPILER_VERSION, profile, script, project_style, voice_guidance,
                                      reference_manifest, reference_labels, compiled["parameters"], mode,
                                      input_contract.get("fingerprint"), input_contract.get("confirmed_downgrades")])
    return compiled
