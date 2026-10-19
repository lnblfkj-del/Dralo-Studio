"""Video configuration tools never generate until an explicit durable trial is confirmed."""

import json
from hashlib import sha256
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.core.outbound_http import PublicTransport
from app.models import Job
from app.providers.protocols import effective_protocol, validate_model_protocol
from app.providers.video_integrations import resolve_video_integration
from app.schemas.video_configuration import VideoConfigInput
from app.schemas.video_tools import VideoPackage, VideoTrialInput
from app.services.video_configuration import read, save_input


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_package(raw):
    if len(json.dumps(raw, allow_nan=False)) > 100000:
        raise ValidationError("接法文件超过100KB")
    package = VideoPackage.model_validate(raw)
    from app.providers.protocols import validate_protocol_type

    validate_protocol_type(package.protocol, "video")
    if package.protocol == "fake_video":
        raise ConflictError("测试用协议不能作为客户接法导入")
    if (package.protocol == "custom_video") != bool(package.video_config.integration):
        raise ConflictError("自定义接法必须包含接口映射；现成接法不接受自定义接口映射")
    from app.services.video_configuration import validate_defaults

    validate_defaults(package.video_config)
    return package.model_dump(mode="json")


async def import_link(url):
    from app.providers.configured_video import safe_public_url

    safe_public_url(url)
    try:
        async with httpx.AsyncClient(transport=PublicTransport(), trust_env=False, timeout=15, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise ConflictError("接法链接未返回文件；不跟随跳转，请使用最终JSON文件链接")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 100000:
                        raise ConflictError("接法文件超过100KB")
        return validate_package(json.loads(raw))
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("无法读取有效接法文件，请检查JSON链接或改用文件导入") from exc


def export_package(provider, model):
    config = read(model)
    if config is None or config.specification is None:
        raise ConflictError("请先补全并保存视频支持规格，再导出")
    writable = VideoConfigInput.model_validate({key: getattr(config, key) for key in VideoConfigInput.model_fields})
    return validate_package({"name": model.name[:80], "protocol": effective_protocol(provider, model),
                             "video_config": writable.model_dump(mode="json")})


def check(provider, model, draft=None):
    if model.model_type != "video":
        raise ConflictError("此入口仅用于视频模型")
    candidate = SimpleNamespace(**{key: getattr(model, key, None) for key in (
        "id", "provider_id", "model_id", "model_type", "api_protocol", "api_base_url", "video_config",
        "default_params", "capabilities", "video_prompt_certifications", "name", "pricing", "enabled",
    )})
    if draft:
        values = draft.model_dump(exclude_unset=True)
        config = values.pop("video_config", None)
        for key, value in values.items():
            setattr(candidate, key, value)
        if config is not None:
            save_input(provider, candidate, config)
    validate_model_protocol(provider, candidate)
    config = read(candidate)
    if not config or not config.specification:
        return {"ready": False, "model_called": False, "issues": ["请补全支持规格"], "modes": []}
    from app.services.video_model_settings import duration_values

    integration = resolve_video_integration(provider, candidate)
    results = []
    for mode in config.specification.modes:
        limits = {limit.role: limit for limit in mode.reference_limits}
        flags = {"first": "first_frame" in limits, "last": "last_frame" in limits,
                 "references": min(limits["image"].maximum, mode.max_total_references - int("first_frame" in limits)
                                   - int("last_frame" in limits)) if "image" in limits else 0}
        issues = []
        for milliseconds in duration_values(mode.durations):
            try:
                parameters = {**config.transport_params, **config.generation_defaults, "duration": milliseconds / 1000,
                              "resolution": mode.resolution, "aspect_ratio": mode.aspect_ratio}
                if parameters["duration"].is_integer():
                    parameters["duration"] = int(parameters["duration"])
                integration.validate_parameters(candidate.model_id, parameters, **flags)
            except ConflictError as exc:
                issues.append(exc.message)
                break
        results.append({"key": mode.key, "input_mode": mode.input_mode, "passed": not issues, "issues": issues})
    return {"ready": all(item["passed"] for item in results), "model_called": False,
            "issues": list(dict.fromkeys(issue for item in results for issue in item["issues"])), "modes": results}


async def trial_plan(session, owner_id, provider, model, payload):
    from app.services import project_service
    from app.services.asset_reference_service import load_reference_images
    from app.services.planning_capability_admin import config_fingerprint
    from app.services.pricing_service import estimate
    from app.services.video_input_compiler import compile_video_input
    from app.services.style_generation_service import project_style_media

    project = await project_service.get_project(session, payload.project_id, owner_id)
    ratio = (project.creation_settings or {}).get("aspect_ratio")
    if ratio and ratio != "default" and payload.parameters.get("aspect_ratio") != ratio:
        raise ConflictError("试生成画幅必须跟随所选项目，不能修改项目画幅")
    if not provider.enabled or not model.enabled or model.model_type != "video":
        raise ConflictError("请先启用视频模型及渠道")
    refs = ([{"media_id": payload.first_frame_media_id, "role": "first_frame"}] if payload.first_frame_media_id else [])
    refs += ([{"media_id": payload.last_frame_media_id, "role": "last_frame"}] if payload.last_frame_media_id else [])
    refs += [{"media_id": item, "role": "reference_image"} for item in payload.reference_media_ids]
    style_id = await project_style_media(session, payload.project_id, owner_id, "video")
    if style_id and payload.parameters.get("input_mode") != "text" and not any(item["media_id"] == style_id for item in refs):
        refs.append({"media_id": style_id, "role": "reference_image", "purpose": "project_style"})
    compiled = compile_video_input(provider, model, refs, payload.parameters)
    if not compiled["ready"]:
        raise ConflictError("；".join(compiled["blockers"]))
    duration = compiled["effective_parameters"].get("duration")
    if type(duration) not in (int, float) or not 0 < duration <= 15:
        raise ConflictError("试生成请明确选择不超过15秒的支持时长")
    if refs:
        await load_reference_images(session, SimpleNamespace(owner_id=owner_id),
                                    list(dict.fromkeys(item["media_id"] for item in refs)), max_images=32)
    quote = estimate(model, payload.prompt, compiled["effective_parameters"])
    token = digest([config_fingerprint(provider, model), project.creation_settings, payload.model_dump(), compiled["fingerprint"], quote])
    return {"confirmation_token": token, "quote": quote, "input_mode": compiled["input_mode"],
            "parameters": compiled["effective_parameters"], "model_called": False,
            "config_fingerprint": config_fingerprint(provider, model)}


async def start_trial(session, owner_id, provider_id, model_id, payload):
    from app.services.planning_capability_admin import lock_catalog
    from app.services.job_creation_service import create_video_job

    provider, model = await lock_catalog(session, provider_id, model_id)
    request = VideoTrialInput.model_validate(payload.model_dump(include=set(VideoTrialInput.model_fields)))
    request_digest = digest([request.model_dump(), payload.confirmation_token])
    existing = await session.scalar(select(Job).where(Job.owner_id == owner_id, Job.provider_id == provider.id,
        Job.payload["video_model_test"]["model_id"].as_integer() == model_id,
        Job.payload["video_model_test"]["request_id"].as_string() == payload.request_id))
    if existing:
        if existing.payload["video_model_test"]["request_digest"] != request_digest:
            raise ConflictError("试生成请求编号已被使用，请重新检查并确认")
        return existing
    plan = await trial_plan(session, owner_id, provider, model, request)
    if plan["confirmation_token"] != payload.confirmation_token:
        raise ConflictError("模型、参数或费用已变化，请重新检查试生成")
    quote = plan["quote"]
    if quote["estimated_cents"] is None:
        if not payload.accept_unknown_price:
            raise ConflictError("价格未知不等于免费，请明确确认未知费用")
    elif quote["estimated_cents"] > payload.max_cost_cents:
        raise ConflictError("预估费用超过已确认金额，请重新确认")
    job = await create_video_job(session, owner_id, provider_model_id=model.id, **request.model_dump(), negative_prompt=None)
    job.max_attempts = 1
    job.payload = {**job.payload, "video_model_test": {"request_id": payload.request_id, "request_digest": request_digest,
        "model_id": model.id, "config_fingerprint": plan["config_fingerprint"], "parameters": plan["parameters"],
        "input_mode": plan["input_mode"], "quote": quote}}
    await session.flush()
    return job


async def trial_history(session, owner_id, model_id):
    from app.core.video_submission import remote_video_task_id
    jobs = (await session.scalars(select(Job).where(Job.owner_id == owner_id,
        Job.payload["video_model_test"]["model_id"].as_integer() == model_id).order_by(Job.id.desc()).limit(20))).all()
    return [{"id": job.id, "status": job.status, "error": job.error_message, "result": job.result,
             "can_resume": job.status == "failed" and bool(remote_video_task_id(job.payload.get("video_submission") or {}))
             and (job.payload.get("video_submission") or {}).get("terminal_status") not in {"failed", "cancelled"},
             "test": job.payload["video_model_test"], "created_at": job.created_at} for job in jobs]
