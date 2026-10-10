"""Read-only bridge from frozen R1 semantics to protocol/prompt previews."""

from types import SimpleNamespace

from app.core.errors import ConflictError, ValidationError
from app.providers.protocols import effective_protocol, is_toapis_model
from app.providers.video_contracts import VIDEO_CONTRACTS
from app.services.episode_planning_assembly import read_assembly
from app.services.episode_planning_capability import CONFIG_KEY
from app.services.episode_planning_contract import fingerprint
from app.services.video_input_compiler import compile_video_input
from app.services.video_model_contract import audio_contract
from app.services.video_prompt_compiler import (
    compile_model_prompt,
    resolve_model_prompt_profile,
    video_prompt_endpoint_fingerprint,
)


def _semantics(segment):
    script = {
        "scene": {},
        "camera": [],
        "performances": [],
        "dialogue": [],
        "audio": {"music": [], "ambience": [], "sound_effects": []},
        "entry_state": segment["entry_state"],
        "exit_state": segment["exit_state"],
    }
    manifest = []
    for index, shot in enumerate(segment["shots"], 1):
        direction = shot["direction"]
        script["camera"].append(
            {
                "shot_id": index,
                "duration": (shot["end_ms"] - shot["start_ms"]) / 1000,
                "start_ms": shot["start_ms"] + segment["safe_head_ms"],
                "end_ms": shot["end_ms"] + segment["safe_head_ms"],
                **{key: direction[key] for key in ("shot_size", "camera_angle", "camera_movement")},
            }
        )
        script["performances"].append({"shot_id": index, "action": direction["action"]})
        events = {event["source_key"]: event for event in shot["timing_events"]}
        for source in shot["sources"]:
            manifest.append({"shot_id": index, "source": source})
            kind = source["kind"]
            event = events.get(source["key"])
            timing = (
                {
                    "start_ms": shot["start_ms"] + segment["safe_head_ms"] + event["start_ms"],
                    "end_ms": shot["start_ms"] + segment["safe_head_ms"]
                    + event["start_ms"] + event["estimated_ms"],
                }
                if event else {}
            )
            if kind in {"dialogue", "narration"}:
                script["dialogue"].append(
                    {
                        "shot_id": index,
                        "speaker": source["speaker"] or "旁白",
                        "text": source["spoken_text"] or source["text"],
                        "tone": "旁白，不作为画面人物台词" if kind == "narration" else "",
                        **timing,
                    }
                )
            elif kind in {"music", "sound"}:
                category = "music" if kind == "music" else "sound_effects"
                script["audio"][category].append({"shot_id": index, "text": source["text"], **timing})
            elif kind == "scene":
                script["scene"]["description"] = (
                    script["scene"].get("description", "") + source["text"] + "\n"
                )
            else:
                script["performances"].append({"shot_id": index, "action": source["text"], **timing})
    return script, manifest


def _audio_parameters(provider, model, segment, mode):
    contract = audio_contract(provider, model)
    required = (
        segment["requires_native_dialogue"]
        or segment["requires_native_audio"]
        or segment["background_music"]
    )
    blockers, parameters = [], {}
    if required:
        if not contract["verified"]:
            blockers.append("原生声音能力尚未按当前渠道验证，不能根据模型名称推测")
        elif contract["output"] == "unsupported":
            blockers.append("该渠道不支持计划必需的原生声音，请选择支持声音的模式或重新规划配音")
        elif segment["requires_native_dialogue"] and not contract["dialogue"]:
            blockers.append("该渠道尚未验证原生对白，不能用有音轨代替对白能力")
        if not blockers and contract["output"] == "optional":
            parameters[contract["parameter"]] = True
    if mode.bgm_control == "parameter":
        blockers.append("该模式声明独立配乐参数，但当前适配器尚未登记该字段，不能用总声音开关代替")
    return parameters, blockers, contract


def adapter_view(model):
    return SimpleNamespace(
        **{
            key: getattr(model, key, None)
            for key in (
                "id",
                "provider_id",
                "model_id",
                "api_protocol",
                "api_base_url",
                "video_prompt_certifications",
                "enabled",
                "model_type",
            )
        },
        default_params={
            key: value
            for key, value in (model.default_params or {}).items()
            if key not in {CONFIG_KEY, "native_audio_capability"}
        },
    )


def compile_preview(
    plan, analysis, stored, *, provider, model, current_capability, include_contract=False
):
    """No DB writes, HTTP, task creation, plan edits, or implicit shot splitting."""
    assembly = read_assembly(plan, analysis, stored)
    cap = plan.capability
    if (
        fingerprint(cap) != fingerprint(current_capability)
        or model.id != cap.provider_model_id
        or model.provider_id != provider.id
        or model.model_id != cap.model_id
        or effective_protocol(provider, model) != cap.protocol
        or video_prompt_endpoint_fingerprint(provider, model) != cap.endpoint_fingerprint
        or not model.enabled
        or not provider.enabled
        or model.model_type != "video"
    ):
        raise ConflictError("模型路由或能力已变化，请先重新核对冻结计划")
    modes = {mode.key: mode for mode in cap.modes}
    # Capability evidence is internal metadata, not a provider request option.
    # Use a detached view: never mutate the live ORM model or its JSON settings.
    adapter_model = adapter_view(model)
    compiled = []
    for segment in assembly["segments"]:
        mode = modes[segment["mode_key"]]
        blockers = []
        prompt = None
        manifest = []
        contract = script = None
        parameters = {
            "duration": segment["requested_duration_ms"] / 1000,
            "aspect_ratio": mode.aspect_ratio,
            "resolution": mode.resolution,
        }
        if parameters["duration"].is_integer():
            parameters["duration"] = int(parameters["duration"])
        audio_parameters, audio_blockers, audio = _audio_parameters(provider, model, segment, mode)
        parameters.update(audio_parameters)
        unsupported = {ref["role"] for ref in segment["references"]} - {
            "image",
            "first_frame",
            "last_frame",
        }
        if unsupported:
            blockers.append("音视频参考尚未接入本编译链路，不能降级为普通参考图")
        if cap.verification != "channel_verified":
            blockers.append("当前规划能力尚未完成真实渠道验证")
        if cap.protocol not in VIDEO_CONTRACTS and not is_toapis_model(provider, model):
            blockers.append("当前视频协议没有已登记的参数适配器")
        if not blockers:
            refs = [
                {
                    "media_id": ref["media_id"],
                    "role": "reference_image" if ref["role"] == "image" else ref["role"],
                }
                for ref in segment["references"]
            ]
            try:
                contract = compile_video_input(provider, adapter_model, refs, parameters)
                blockers.extend(contract["blockers"])
                if contract["required_confirmations"]:
                    blockers.append("冻结素材用途不可自动降级，请重新规划")
                actual_refs = [
                    (ref["media_id"], ref["role"]) for ref in contract["effective_references"]
                ]
                if actual_refs != [(ref["media_id"], ref["role"]) for ref in refs]:
                    blockers.append("适配器改变了冻结素材集合或顺序")
                if any(
                    contract["effective_parameters"].get(key) != value
                    for key, value in parameters.items()
                ):
                    blockers.append("适配器改变了冻结的时长、比例或分辨率")
                if contract["input_mode"] != mode.input_mode:
                    blockers.append("实际素材输入模式与冻结模式不一致")
                if not blockers:
                    policy = {"background_music": segment["background_music"]}
                    contract["audio_policy"] = policy
                    script, manifest = _semantics(segment)
                    profile = resolve_model_prompt_profile(provider, adapter_model, mode.input_mode)
                    preview = compile_model_prompt(script, profile=profile, input_contract=contract)
                    prompt = preview["prompt"]
                    blockers.extend(preview["blockers"])
                    if preview["submission_plan"] != "single_request":
                        blockers.append(
                            "现有提示词档案要求拆镜头；冻结计划禁止自动拆段，需核验该模型的多镜头方案"
                        )
                    if (
                        profile["verification"] != "channel_verified"
                        or not profile["production_enabled"]
                    ):
                        blockers.append("该模型提示词档案尚未通过生产验收")
                    blockers.extend(preview["warnings"])
            except (ConflictError, ValidationError, ValueError) as exc:
                blockers.append(str(exc))
        blockers.extend(audio_blockers)
        compiled.append(
            {
                "segment_key": segment["key"],
                "parameters": parameters,
                "prompt": prompt,
                "source_manifest": manifest,
                "audio": audio,
                "blockers": list(dict.fromkeys(blockers)),
                # Never expose the old compiler's per-shot submission plan or default
                # parameters (which contain internal capability metadata).
                "submission_plan": "frozen_segment",
                "video_submission_ready": False,
                **(
                    {"input_contract": contract, "structured_script": script}
                    if include_contract
                    else {}
                ),
            }
        )
    return {
        "contract_version": "episode-video-preview.v1",
        "plan_fingerprint": fingerprint(plan),
        "assembly_fingerprint": assembly["fingerprint"],
        "segments": compiled,
        "video_submission_ready": False,
        "pending_checks": [
            "媒体文件及权限复核",
            "声音与首尾帧真实渠道效果验收",
            "生产提交与收费确认",
        ],
    }
