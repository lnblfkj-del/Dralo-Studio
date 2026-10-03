"""Shared, authenticated submission boundary for canvas nodes and Agent tools."""

import hashlib
import json
import re

from sqlalchemy import select, update
from app.services.team_access import owner_scope, same_team

from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, CanvasEdge, Job, MediaFile, Project, Provider, ProviderModel
from app.providers.protocols import execution_contract, effective_protocol, is_toapis_model, validate_model_protocol
from app.providers.video_contracts import VIDEO_CONTRACTS
from app.services import business_executor_service, canvas_service, job_service
from app.services.asset_binding_service import resolve_asset_binding
from app.services.pricing_service import attach as attach_pricing


async def submission_lock(session, project):
    # SQLite is our queue database. Take its write lock before idempotency/sequence checks.
    await session.execute(update(Project).where(Project.id == project.id).values(name=Project.name))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def existing_request(session, project, request_id, digest):
    if not request_id:
        return None
    job = await session.scalar(select(Job).where(
        Job.owner_id == project.owner_id, Job.project_id == project.id,
        Job.payload["parameters"]["canvas_request_id"].as_string() == request_id,
    ))
    if job and job.payload.get("parameters", {}).get("canvas_request_digest") != digest:
        raise ConflictError("请求编号已用于其他内容，请重新提交")
    return job


async def optimize_text(session, project, node_key, payload):
    from app.services import provider_service
    settings = await provider_service.get_ai_settings(session)
    if not settings.canvas_agent_enabled:
        raise ConflictError("请先启用画布 Agent 配置")
    skills = await provider_service.get_agent_skills(session, "canvas")
    if not skills and not (settings.agent_skill_bindings or {}).get("canvas"):
        from app.services.agent_config_service import list_skills
        skills = await list_skills(session, "canvas")
    from app.services.skill_runtime import text_optimization_skill
    skill = text_optimization_skill(skills)
    if not skill:
        raise ConflictError("请在画布 Agent 设置中启用文本 Skill")
    preferred = settings.canvas_agent_text_model_id or settings.default_text_model_id
    if preferred:
        payload = payload.model_copy(update={"provider_model_id": preferred})
    await submission_lock(session, project)
    digest = fingerprint([node_key, "optimize_text", payload.model_dump()])
    previous = await existing_request(session, project, payload.request_id, digest)
    if previous:
        return previous
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    if node.node_type != "text" or not payload.prompt.strip():
        raise ConflictError("仅支持优化非空文本节点")
    active = await session.scalar(select(Job.id).where(Job.target_type == "canvas_text_optimize", Job.target_id == node.id, Job.status.not_in(job_service.TERMINAL_STATUSES)))
    if active:
        raise ConflictError("该文本正在优化，请等待或取消")
    refs, context = await resolve_references(session, project, payload.parameters, node)
    if refs or any(item.get("purpose") != "script" for item in context):
        raise ConflictError("文本优化只接受文本上下文")
    # Direct inputs only: do not execute or recursively expand referenced instructions.
    context_text = "\n\n".join(item.get("content", "") for item in context)
    if len(context_text) + len(payload.prompt) > 100_000:
        raise ConflictError("优化输入过长，请缩短文本")
    job = await job_service.create_text_job(session, project.owner_id,
        project_id=project.id, provider_model_id=payload.provider_model_id,
        prompt="画布文本 Skill 背景：\n" + (skill.instruction or "") + "\n本次动作限定为：优化以下提示词或拍摄脚本，只返回优化后的正文。不执行工具、不输出执行计划、不生成图片或视频。保留正文中的所有 @{节点ID} 引用标记，不新增标记。\n参考上下文（仅供理解）：\n" + context_text + "\n\n待优化正文：\n" + payload.prompt,
        parameters={"canvas_request_id": payload.request_id, "canvas_request_digest": digest,
                    "optimization_original": payload.prompt, "source_node_key": node_key, "asset_context": context,
                    "skill_id": skill.id, "skill_key": skill.key, "skill_version": skill.version})
    job.target_type, job.target_id = "canvas_text_optimize", node.id
    node.data = {**node.data, "job_id": job.id, "generation_status": job.status}
    await session.flush()
    return job


async def validate_model(session, model_id, task_type, parameters, *, validate_parameters=True):
    model = await session.get(ProviderModel, model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if not model or not provider:
        raise NotFoundError("模型不存在")
    if not model.enabled or not provider.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != ("tts" if task_type == "audio" else task_type):
        raise ConflictError("模型输出类型与任务不匹配")
    validate_model_protocol(provider, model)
    if task_type == "image" and is_toapis_model(provider, model) and model.model_id == "gpt-image-2":
        from app.providers import toapis_image
        toapis_image.parameters(model.model_id, {**model.default_params, **parameters})
    if validate_parameters:
        for field, choices in (("aspect_ratio", "aspect_ratios"), ("resolution", "resolutions"), ("duration", "durations")):
            value, allowed = parameters.get(field), model.default_params.get(choices)
            if value not in (None, "default", "模型默认") and isinstance(allowed, list) and value not in allowed:
                raise ConflictError(f"模型不支持参数 {field}={value}")
    return model


async def resolve_references(session, project, parameters, node=None, context_only=False):
    raw_refs = parameters.get("references", [])
    if not isinstance(raw_refs, list):
        raise ConflictError("参考素材必须是列表")
    refs = list(raw_refs)
    for field in ("attachment_media_ids", "attachment_asset_ids"):
        values = parameters.get(field, [])
        if not isinstance(values, list) or len(values) > 32 or any(type(value) is not int or value <= 0 for value in values):
            raise ConflictError("引用 ID 必须是至多 32 项正整数列表")
    if len(refs) > 32 or not all(isinstance(ref, dict) for ref in refs):
        raise ConflictError("参考素材格式不正确或超过 32 项")
    for media_id in parameters.get("attachment_media_ids", []):
        refs.append({"media_id": media_id, "role": "reference_image"})
    asset_context = []
    for asset_id in parameters.get("attachment_asset_ids", []):
        asset = await session.scalar(select(Asset).where(Asset.id == asset_id, owner_scope(Asset.owner_id, project.owner_id)))
        if not asset:
            raise NotFoundError("引用资产不存在或无权访问")
        binding = await resolve_asset_binding(
            session, project, asset_id=asset.id, role="reference_image", expected_kind="image"
        )
        asset_context.append({
            "id": asset.id, "name": asset.name,
            "content": asset.prompt_anchor or asset.description or "",
            "purpose": "asset", **binding,
        })
        refs.append(binding)
    mention_keys = parameters.get("node_mentions", [])
    if not isinstance(mention_keys, list) or len(mention_keys) > 32 or any(not isinstance(value, str) or not value or len(value) > 64 for value in mention_keys):
        raise ConflictError("节点引用必须是至多 32 项有效节点 ID")

    async def source_reference(source, role):
        media_id = (source.data or {}).get("media_id")
        asset_id = None
        if source.node_type in {"character", "scene", "costume", "prop", "voice"}:
            asset_id = (source.data or {}).get("production_asset_id")
        elif source.node_type == "asset":
            asset_id = (source.data or {}).get("entity_id")
        if type(asset_id) is int:
            expected = "audio" if role in {"audio_reference", "voice_reference"} else "image"
            return await resolve_asset_binding(
                session,
                project,
                asset_id=asset_id,
                adoption_key=(source.data or {}).get("adoption_key"),
                role=role,
                expected_kind=expected,
            )
        return {"media_file_id": media_id, "media_id": media_id, "role": role}

    async def consume_source(source, purpose):
        if purpose in {"script", "director_shot_package", "audio_track"}:
            source_ref = await source_reference(source, "audio_reference") if purpose == "audio_track" else {}
            media_id = source_ref.get("media_file_id")
            asset_context.append({
                "id": source.node_key,
                "name": (source.data or {}).get("title") or source.node_type,
                "content": (source.data or {}).get("content") or "",
                "purpose": purpose,
                **({"media_id": media_id} if media_id else {}),
                **({key: source_ref[key] for key in (
                    "asset_id", "asset_version_id", "adoption_key", "resolved_revision"
                ) if source_ref.get(key) is not None}),
                **({"director_document": (source.data or {}).get("upstream_director_document") or (source.data or {}).get("director_document"),
                    "upstream": bool((source.data or {}).get("upstream_director_document"))} if purpose == "director_shot_package" else {}),
            })
            return
        mapped_role = {
            "character_reference": "reference_image",
            "scene_reference": "reference_image",
            "costume_reference": "reference_image",
            "prop_reference": "reference_image",
            "style_reference": "reference_image",
            "reference_image": "reference_image",
            "first_frame": "first_frame",
            "last_frame": "last_frame",
        }.get(purpose)
        if mapped_role:
            source_ref = await source_reference(source, mapped_role)
            refs.append({
                **source_ref,
                "media_id": source_ref.get("media_file_id"),
                "role": mapped_role,
                "node_id": source.node_key,
                "purpose": purpose,
            })

    if node:
        targets = [node.node_key]
        if node.data.get("source_prompt_id"):
            targets.append(node.data["source_prompt_id"])
        edges = (await session.scalars(select(CanvasEdge).where(
            CanvasEdge.canvas_id == node.canvas_id, CanvasEdge.target_key.in_(targets),
        ))).all()
        consumed = set()
        for edge in sorted(edges, key=lambda edge: edge.source_key):
            purpose = (edge.data or {}).get("purpose") or (edge.data or {}).get("reference_role") or "organization"
            if purpose != "organization":
                source = await canvas_service.get_canvas_node(session, project.id, edge.source_key)
                await consume_source(source, purpose)
                consumed.add((source.node_key, purpose))
        for mention_key in mention_keys:
            source = await canvas_service.get_canvas_node(session, project.id, mention_key)
            if source.node_key == node.node_key:
                raise ConflictError("节点不能引用自身")
            purpose = {
                "character": "character_reference", "scene": "scene_reference", "costume": "costume_reference", "prop": "prop_reference", "image": "reference_image",
                "voice": "audio_track",
                "text": "script", "episode": "script", "shot": "script", "audio": "audio_track",
                "director": "director_shot_package",
            }.get(source.node_type, "organization")
            if purpose != "organization" and (source.node_key, purpose) not in consumed:
                await consume_source(source, purpose)
                consumed.add((source.node_key, purpose))
    result, seen = [], set()
    for ref in refs:
        if type(ref.get("asset_id")) is int:
            ref = {
                **ref,
                **await resolve_asset_binding(
                    session,
                    project,
                    asset_id=ref["asset_id"],
                    adoption_key=ref.get("adoption_key"),
                    asset_version_id=ref.get("asset_version_id"),
                    media_file_id=ref.get("media_file_id") or ref.get("media_id"),
                    role=ref.get("role", "reference_image"),
                ),
            }
            ref["media_id"] = ref["media_file_id"]
        media_id, role = ref.get("media_id"), ref.get("role", "reference_image")
        if type(media_id) is not int or role not in {"reference_image", "first_frame", "last_frame", "audio_reference", "voice_reference"}:
            raise ConflictError("引用需指定有效媒体和用途")
        media = await session.scalar(select(MediaFile).where(MediaFile.id == media_id, owner_scope(MediaFile.owner_id, project.owner_id)))
        if not media:
            raise NotFoundError("引用素材不存在或无权访问")
        expected = "audio" if role in {"audio_reference", "voice_reference"} else "image"
        if media.kind != expected and not context_only:
            raise ConflictError(f"引用用途 {role} 需要 {expected} 素材")
        if (media_id, role) not in seen:
            result.append({**ref, "name": media.original_name, "kind": media.kind})
            seen.add((media_id, role))
    unique_context = []
    context_seen = set()
    for item in asset_context:
        key = (str(item.get("id")), item.get("purpose", "asset"))
        if key not in context_seen:
            unique_context.append(item)
            context_seen.add(key)
    return result, unique_context


async def submit_media(session, project, *, node_key, task_type, provider_model_id, prompt, parameters, request_id=None):
    parameters = dict(parameters or {})
    expected_video_preflight = parameters.pop("video_preflight_fingerprint", None)
    if task_type in {"image", "video"} and parameters.get("aspect_ratio") in (None, "", "default", "模型默认"):
        project_ratio = (project.creation_settings or {}).get("aspect_ratio")
        if isinstance(project_ratio, str) and project_ratio not in {"", "default", "模型默认"}:
            parameters["aspect_ratio"] = project_ratio
    await submission_lock(session, project)
    digest = fingerprint([node_key, task_type, provider_model_id, prompt, parameters])
    prior = await existing_request(session, project, request_id, digest)
    if prior:
        return prior
    orchestrator_execution = await business_executor_service.resolve_execution(
        session, "media_task_orchestrator"
    )
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    allowed_node_types = {task_type}
    if task_type == "image":
        allowed_node_types.update({"character", "scene", "costume", "prop"})
    if task_type == "audio":
        allowed_node_types.add("voice")
    if node.node_type not in allowed_node_types:
        raise ConflictError("目标节点已锁定或类型不匹配")
    if node.node_type in {"character", "scene", "costume", "prop", "voice"}:
        asset = await session.get(Asset, node.data.get("production_asset_id"))
        if not asset or not await same_team(session, asset.owner_id, project.owner_id) or asset.project_id != project.id or asset.asset_type != node.node_type:
            raise ConflictError("只能在当前项目可编辑且类型匹配的共享资产节点中生成素材")
    if node.parent_key:
        parent = await canvas_service.get_canvas_node(session, project.id, node.parent_key)
        if parent.locked:
            raise ConflictError("目标节点所属分组已锁定")
    active = await session.scalar(select(Job.id).where(
        Job.project_id == project.id, Job.target_type == "canvas_node", Job.target_id == node.id,
        Job.status.not_in(job_service.TERMINAL_STATUSES),
    ))
    if active:
        raise ConflictError("节点已有进行中的任务，请等待完成或取消")
    model = await validate_model(
        session, provider_model_id, task_type, parameters,
        validate_parameters=task_type != "video",
    )
    refs, assets = await resolve_references(session, project, parameters, node)
    provider = await session.get(Provider, model.provider_id)
    video_compilation = None
    if task_type == "video":
        from app.services.canvas_video_compiler import compile_video_submission
        video_compilation = await compile_video_submission(
            session, project, model, provider, prompt, parameters, refs, assets
        )
        if not video_compilation["ready"]:
            raise ConflictError("视频提交预检未通过：" + "；".join(video_compilation["blockers"]))
        if expected_video_preflight and expected_video_preflight != video_compilation["submission_fingerprint"]:
            raise ConflictError("导演工程、引用素材或模型能力已变化，请重新查看视频输入预检")
        refs = video_compilation["effective_references"]
    if task_type == "video" and refs and effective_protocol(provider, model) not in {"fake_video", *VIDEO_CONTRACTS} and not is_toapis_model(provider, model):
        raise ConflictError("当前视频适配器仅实现文生视频；首尾帧/参考图上传协议尚未接入，未提交收费任务")
    if len({ref["media_id"] for ref in refs}) > 4:
        raise ConflictError("当前适配器最多支持 4 个参考媒体（含首尾帧）")
    # Never silently drop a reference that the current adapters cannot send.
    if any(ref["role"] in {"audio_reference", "voice_reference"} for ref in refs):
        raise ConflictError("当前生成适配器尚未接入音频/音色参考；素材保留，未提交收费任务")
    if task_type == "image" and refs and "reference_images" not in model.capabilities:
        raise ConflictError("此图片模型未声明参考图能力")
    if task_type == "image" and any(ref["role"] != "reference_image" for ref in refs):
        raise ConflictError("首尾帧只适用于视频任务")
    frames = {}
    for role in ("first_frame", "last_frame"):
        matches = [ref["media_id"] for ref in refs if ref["role"] == role]
        if len(matches) > 1:
            raise ConflictError("首帧和尾帧各只能选择一个")
        frames[role] = matches[0] if matches else None
        if matches and model.default_params.get(f"supports_{role}") is False:
            raise ConflictError(f"模型不支持 {role}")
    frozen_context = [
        {key: item.get(key) for key in (
            "id", "name", "content", "purpose", "media_id", "asset_id",
            "asset_version_id", "adoption_key", "resolved_revision",
        ) if item.get(key) is not None}
        for item in assets
    ]
    prompt_context = [
        item for item in frozen_context
        if item.get("purpose") not in {"director_shot_package", "audio_track"}
    ]
    params = {**parameters, "references": refs, "asset_context": frozen_context,
              "canvas_request_id": request_id, "canvas_request_digest": digest,
              "source_node_key": node_key,
              "execution": {"model_id": model.model_id, "provider_model_id": model.id, "task_type": task_type,
                            "skill_application": "not_applied_to_media_prompt",
                            "prompt_source": "submitted_content_and_resolved_references"}}
    if video_compilation:
        params["video_compilation"] = video_compilation
        params["director_shot_package"] = video_compilation["director_shot_package"]
        params["video_input_contract"] = video_compilation["video_input_contract"]
        params["video_input_confirmations"] = video_compilation["video_input_contract"].get(
            "confirmed_downgrades", []
        )
    reference_ids = [ref["media_id"] for ref in refs if ref["role"] == "reference_image"]
    frozen_bindings = [
        {key: ref.get(key) for key in (
            "asset_id", "asset_name", "asset_type", "adoption_key", "asset_version_id",
            "media_file_id", "media_kind", "view_type", "view_label", "adoption_origin",
            "resolved_revision", "role", "node_id", "purpose",
        ) if ref.get(key) is not None}
        for ref in refs if ref.get("asset_id") is not None
    ]
    # Stable node tokens are bindings, not words to send or read aloud.
    local_prompt = re.sub(r"@\{[^}]+\}", "", prompt).strip()
    for item in sorted(prompt_context, key=lambda item: len(item.get("name", "")), reverse=True):
        if item.get("name"):
            local_prompt = local_prompt.replace("@" + item["name"], "")
    if task_type == "audio":
        prompt = "\n\n".join([item["content"].strip() for item in prompt_context if item.get("purpose") == "script" and item.get("content", "").strip()] + ([local_prompt.strip()] if local_prompt.strip() else []))
    else:
        prompt = (video_compilation["effective_prompt"] if video_compilation else local_prompt.strip()) + ("\n\n参考资产：" + json.dumps(prompt_context, ensure_ascii=False) if prompt_context else "")
    if len(prompt) > 100_000:
        raise ConflictError("合并后的提示词超过 100000 字，请缩短输入")
    if not prompt:
        raise ConflictError("请输入生成要求")
    if task_type == "image":
        job = await job_service.create_image_job(session, project.owner_id, project_id=project.id,
            asset_id=node.id, provider_model_id=model.id, prompt=prompt, negative_prompt=None,
            reference_media_ids=reference_ids, parameters=params)
    elif task_type == "audio":
        from app.providers.speech import speech_parameters
        if refs:
            raise ConflictError("文字配音仅使用预设音色，不支持参考图或音色克隆")
        speech = speech_parameters(model, parameters, prompt)
        job = Job(owner_id=project.owner_id, project_id=project.id, provider_id=model.provider_id,
            job_type="tts", status="queued", provider=provider.name, model=model.model_id,
            payload={"protocol_contract": execution_contract(provider, model), "provider_model_id": model.id, "prompt": prompt, "parameters": {**params, **speech}})
        attach_pricing(job, model)
        session.add(job)
        await session.flush()
    elif task_type == "video":
        job = await job_service.create_video_job(session, project.owner_id, project_id=project.id,
            provider_model_id=model.id, prompt=prompt, negative_prompt=None,
            first_frame_media_id=frames["first_frame"], last_frame_media_id=frames["last_frame"],
            reference_media_ids=reference_ids, parameters=params)
    else:
        raise ConflictError("该媒体生成能力尚未接入")
    job.target_type, job.target_id = "canvas_node", node.id
    job.payload = {
        **job.payload,
        "asset_bindings": frozen_bindings,
        "business_executor": orchestrator_execution,
        "business_tool": "canvas.media.generate",
    }
    native_budget = parameters.get("max_cost")
    if native_budget is not None:
        from app.services.pricing_service import number
        quote = job.payload.get("pricing_snapshot", {})
        try:
            valid_budget = isinstance(native_budget, dict) and native_budget.get("currency") == quote.get("currency") and quote.get("amount") is not None and number(quote["amount"]) <= number(native_budget.get("amount"))
        except ValueError:
            valid_budget = False
        if not valid_budget:
            raise ConflictError("费用未知、币种不匹配或超过本次费用上限，任务未提交")
    budget = parameters.get("max_cost_cents")
    if budget is not None:
        if type(budget) not in (int, float) or budget < 0:
            raise ConflictError("费用上限必须为非负数字（分）")
        if job.cost_estimate is None or job.cost_estimate > budget:
            raise ConflictError("费用未知或超过本次费用上限，任务未提交")
    node.data = {**node.data, "job_id": job.id, "generation_status": job.status}
    await session.flush()
    return job


async def preflight_video(session, project, *, node_key, provider_model_id, prompt, parameters):
    """Read-only authoritative preview; submit_media recompiles before creating a job."""
    from app.services.canvas_video_compiler import compile_video_submission

    parameters = dict(parameters or {})
    if parameters.get("aspect_ratio") in (None, "", "default", "模型默认"):
        ratio = (project.creation_settings or {}).get("aspect_ratio")
        if isinstance(ratio, str) and ratio not in {"", "default", "模型默认"}:
            parameters["aspect_ratio"] = ratio
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    if node.node_type != "video":
        raise ConflictError("只有视频节点可以执行视频预检")
    model = await validate_model(
        session, provider_model_id, "video", parameters, validate_parameters=False
    )
    provider = await session.get(Provider, model.provider_id)
    refs, contexts = await resolve_references(session, project, parameters, node)
    return await compile_video_submission(
        session, project, model, provider, prompt, parameters, refs, contexts
    )
