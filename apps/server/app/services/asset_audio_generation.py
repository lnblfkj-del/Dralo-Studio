"""Project audio asset submission, guarded reuse and candidate-only persistence."""

# ruff: noqa: RUF001
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, or_, select, update

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.media_quota import require_media_budget
from app.core.storage_safety import write_storage_bytes
from app.models import Asset, AssetVersion, Job, MediaFile, ProjectMediaLink
from app.providers.audio_contracts import music_parameters
from app.providers.protocols import effective_protocol, execution_contract
from app.providers.speech import speech_parameters
from app.services import (
    asset_production_service,
    asset_service,
    canvas_generation_service,
    job_service,
)
from app.services.asset_audio_profile import effective_profile
from app.services.pricing_service import attach as attach_pricing


async def target(session, job):
    asset = await asset_service.get_asset(session, job.project_id, job.target_id)
    if job.payload.get("operation") == "voice_design":
        from app.services.voice_design_service import target as voice_target
        return await voice_target(session, job.project_id, job.target_id)
    if asset.project_id != job.project_id or asset.asset_type != "voice":
        raise ConflictError("只能向当前项目的声音资产保存音频")
    link = await asset_production_service.ensure_active_link(session, job.project_id, asset.id)
    usage = effective_profile(asset, link.production_data).get("audio_usage")
    if usage != job.payload.get("audio_usage"):
        raise ConflictError("声音用途已改变，请核对后恢复保存；不会重新调用模型")
    return asset, link


async def latest(session, project, asset_id):
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    await asset_service.get_asset(session, project.id, asset_id)
    return await session.scalar(select(Job).where(
        Job.project_id == project.id, Job.target_type == "asset", Job.target_id == asset_id,
        Job.job_type.in_({"tts", "audio"}), Job.deleted_at.is_(None),
        or_(Job.payload["operation"].as_string().is_distinct_from("voice_design"),
            Job.payload["parameters"]["audio_request_id"].as_string().is_not(None)),
    ).order_by(Job.id.desc()).limit(1))


async def submit(session, project, asset_id, payload):
    await canvas_generation_service.submission_lock(session, project)
    # Missing additive fields must preserve the digest of pre-AT6 pending requests.
    request = payload.model_dump(mode="json", exclude={"request_id", "audio_purpose", "voice_description"})
    request.update({key: getattr(payload, key) for key in ("audio_purpose", "voice_description") if getattr(payload, key) is not None})
    digest = canvas_generation_service.fingerprint([asset_id, request])
    prior = await session.scalar(select(Job).where(
        Job.project_id == project.id, Job.target_type == "asset",
        Job.payload["parameters"]["audio_request_id"].as_string() == str(payload.request_id),
    ))
    if prior:
        if prior.payload["parameters"].get("audio_request_digest") != digest:
            raise ConflictError("请求编号已用于其他音频内容，请重新确认")
        return prior
    asset = await asset_service.get_asset(session, project.id, asset_id)
    link = await asset_production_service.ensure_active_link(session, project.id, asset_id)
    if asset.project_id != project.id or asset.asset_type != "voice":
        raise ConflictError("请先创建当前项目的声音资产，不直接修改复用素材")
    if link.production_revision != payload.expected_revision:
        raise ConflictError("资产资料已变化，请刷新后再确认生成")
    usage = effective_profile(asset, link.production_data).get("audio_usage")
    purpose = payload.audio_purpose
    if purpose is not None and purpose not in ({"voice_reference", "dialogue_audio"} if usage == "voice" else {usage}):
        raise ConflictError("生成用途与当前声音资产用途不符")
    if purpose != "voice_reference" and payload.voice_description is not None:
        raise ConflictError("只有参考声线生成使用声线描述")
    if usage not in {"voice", "music", "ambience", "sfx"}:
        raise ConflictError("请先明确声音用途")
    active = await session.scalar(select(Job.id).where(
        Job.project_id == project.id, Job.target_type == "asset", Job.target_id == asset.id,
        Job.status.not_in(job_service.TERMINAL_STATUSES),
    ))
    if active:
        raise ConflictError("该声音资产已有进行中的任务")
    from app.models import Provider, ProviderModel
    model = await session.get(ProviderModel, payload.provider_model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    design = bool(provider and purpose == "voice_reference" and effective_protocol(provider, model) == "elevenlabs_tts"
        and (payload.parameters.get("design_model") or not payload.parameters.get("voice")))
    if design:
        from app.services.audio_verification_service import require_ready
        from app.services.voice_design_service import design_model
        provider, model = await design_model(session, payload.provider_model_id)
        require_ready(provider, model, require_voice=False)
        if not payload.voice_description or not 20 <= len(payload.voice_description.strip()) <= 1000 or not 100 <= len(payload.prompt.strip()) <= 500:
            raise ConflictError("音色设计需 20–1000 字符描述及 100–500 字符试听文本")
        if set(payload.parameters) - {"design_model"} or payload.parameters.get("design_model", "eleven_ttv_v3") not in {"eleven_ttv_v3", "eleven_multilingual_ttv_v2"}:
            raise ConflictError("音色设计只接受设计模型，不使用预设音色参数")
    else:
        model = await canvas_generation_service.validate_model(session, payload.provider_model_id, "audio", payload.parameters)
        provider = await session.get(Provider, model.provider_id)
    protocol = effective_protocol(provider, model)
    if usage in {"voice", "music"} and (usage == "music") != (model.model_type == "audio"):
        raise ConflictError("配音须选择 TTS 模型，配乐或歌曲须选择音乐模型")
    if design:
        parameters = {"design_model": payload.parameters.get("design_model", "eleven_ttv_v3"), "text": payload.prompt}
        sample_method = "voice_design"
    elif usage == "voice":
        from app.services.asset_speech_binding import require_scope
        profile = effective_profile(asset, link.production_data)
        require_scope(profile.get("speech_preset"), provider, model,
                      payload.parameters.get("voice", model.default_params.get("voice")))
        raw_parameters = payload.parameters
        sample_method = None
        if purpose == "voice_reference":
            from app.services.audio_purpose import reference_parameters
            if len(payload.prompt.strip()) > 500:
                raise ConflictError("参考样本试听文本最多 500 字符，无需提交完整剧本台词")
            raw_parameters, sample_method = reference_parameters(model, protocol, raw_parameters, payload.voice_description, provider=provider)
        parameters = {**raw_parameters, **speech_parameters(model, raw_parameters, payload.prompt, protocol=protocol, provider=provider)}
    else:
        parameters = {**payload.parameters, **({"audio_usage": usage} if usage in {"ambience", "sfx"} else {})}
        if usage in {"ambience", "sfx"} and protocol != "stepfun_gen":
            raise ConflictError("环境声与音效请选择 StepFun 音频生成协议，不使用 TTS 朗读描述")
        music_parameters(model, parameters, payload.prompt, protocol)
        purpose = usage
    from app.services.canvas_processing_service import executables
    executables()  # Fail before a paid call when local decoding is unavailable.
    job = Job(owner_id=project.owner_id, project_id=project.id, provider_id=provider.id,
        job_type="tts" if usage == "voice" else "audio", target_type="asset", target_id=asset.id,
        status="queued", provider=provider.name, model=model.model_id,
        payload={"provider_model_id":model.id, "prompt":payload.prompt,
                 "protocol_contract":execution_contract(provider,model), "audio_usage":usage,
                 **({"audio_purpose": purpose} if purpose else {}),
                 **({"voice_description": payload.voice_description.strip(), "voice_sample_method": sample_method} if purpose == "voice_reference" else {}),
                 "parameters":{**parameters,"audio_request_id":str(payload.request_id),"audio_request_digest":digest}})
    attach_pricing(job, model)
    if design:
        from app.services.audio_verification_service import check_fingerprint
        job.payload = {**job.payload, "operation": "voice_design", "prompt": payload.voice_description.strip(),
            "voice_scope": check_fingerprint(provider, model)}
    session.add(job)
    await session.flush()
    return job


async def finalize(session, job, result):
    asset, _ = await target(session, job)
    await session.execute(update(Asset).where(Asset.id == asset.id).values(updated_at=func.current_timestamp()))
    prior = await session.scalar(select(AssetVersion).where(AssetVersion.asset_id == asset.id, AssetVersion.source_job_id == job.id))
    if prior:
        return {"asset_id":asset.id,"asset_version_id":prior.id,"media_file_id":prior.media_file_id,"already_persisted":True}
    data = result["audio_bytes"]
    relative = Path("projects") / str(job.project_id) / "assets" / str(asset.id) / f"audio-job-{job.id}.mp3"
    await require_media_budget(session,len(data))
    write_storage_bytes(settings,settings.storage_path / relative,data)
    purpose = job.payload.get("audio_purpose")
    media = MediaFile(owner_id=job.owner_id,project_id=job.project_id,kind="audio",source="generation",purpose=purpose or "creative",
        file_path=relative.as_posix(),original_name=f"{asset.name}-{job.id}.mp3",mime_type="audio/mpeg",
        size=len(data),hash=sha256(data).hexdigest(),duration=result["duration"])
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=job.project_id,media_file_id=media.id))
    count = await session.scalar(select(func.coalesce(func.max(AssetVersion.version),0)).where(AssetVersion.asset_id==asset.id))
    version = AssetVersion(asset_id=asset.id,media_file_id=media.id,source_job_id=job.id,version=int(count)+1,
        prompt=job.payload["prompt"],parameters={**job.payload["parameters"], **({"audio_purpose":purpose} if purpose else {}),
            **({"voice_description":job.payload["voice_description"], "voice_sample_method":job.payload["voice_sample_method"]} if purpose == "voice_reference" else {})},view_type="base",
        view_label={"voice_reference": "参考声线候选", "ambience": "环境声候选", "sfx": "音效候选"}.get(purpose, "配乐候选" if job.job_type=="audio" else "配音候选"),review_status="candidate",is_final=False)
    session.add(version)
    await session.flush()
    return {"asset_id":asset.id,"asset_version_id":version.id,"media_file_id":media.id,
            "duration":media.duration,"media_url":f"/api/media/{media.id}"}
