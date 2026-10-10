"""Project audio asset submission, guarded reuse and candidate-only persistence."""

# ruff: noqa: RUF001
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select, update

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
    ).order_by(Job.id.desc()).limit(1))


async def submit(session, project, asset_id, payload):
    await canvas_generation_service.submission_lock(session, project)
    digest = canvas_generation_service.fingerprint([asset_id, payload.model_dump(mode="json", exclude={"request_id"})])
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
    if usage not in {"voice", "music"}:
        raise ConflictError("请先明确声音用途为配音或配乐；本期未开放环境声与音效生成")
    active = await session.scalar(select(Job.id).where(
        Job.project_id == project.id, Job.target_type == "asset", Job.target_id == asset.id,
        Job.status.not_in(job_service.TERMINAL_STATUSES),
    ))
    if active:
        raise ConflictError("该声音资产已有进行中的任务")
    from app.models import Provider
    model = await canvas_generation_service.validate_model(session, payload.provider_model_id, "audio", payload.parameters)
    provider = await session.get(Provider, model.provider_id)
    protocol = effective_protocol(provider, model)
    if (usage == "music") != (model.model_type == "audio"):
        raise ConflictError("配音须选择 TTS 模型，配乐须选择纯器乐模型")
    if usage == "voice":
        parameters = {**payload.parameters, **speech_parameters(model, payload.parameters, payload.prompt, protocol=protocol)}
    else:
        music_parameters(model, payload.parameters, payload.prompt, protocol)
        parameters = dict(payload.parameters)
    from app.services.canvas_processing_service import executables
    executables()  # Fail before a paid call when local decoding is unavailable.
    job = Job(owner_id=project.owner_id, project_id=project.id, provider_id=provider.id,
        job_type="audio" if usage == "music" else "tts", target_type="asset", target_id=asset.id,
        status="queued", provider=provider.name, model=model.model_id,
        payload={"provider_model_id":model.id, "prompt":payload.prompt,
                 "protocol_contract":execution_contract(provider,model), "audio_usage":usage,
                 "parameters":{**parameters,"audio_request_id":str(payload.request_id),"audio_request_digest":digest}})
    attach_pricing(job, model)
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
    media = MediaFile(owner_id=job.owner_id,project_id=job.project_id,kind="audio",source="generation",
        file_path=relative.as_posix(),original_name=f"{asset.name}-{job.id}.mp3",mime_type="audio/mpeg",
        size=len(data),hash=sha256(data).hexdigest(),duration=result["duration"])
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=job.project_id,media_file_id=media.id))
    count = await session.scalar(select(func.coalesce(func.max(AssetVersion.version),0)).where(AssetVersion.asset_id==asset.id))
    version = AssetVersion(asset_id=asset.id,media_file_id=media.id,source_job_id=job.id,version=int(count)+1,
        prompt=job.payload["prompt"],parameters=job.payload["parameters"],view_type="base",
        view_label="配乐候选" if job.job_type=="audio" else "配音候选",review_status="candidate",is_final=False)
    session.add(version)
    await session.flush()
    return {"asset_id":asset.id,"asset_version_id":version.id,"media_file_id":media.id,
            "duration":media.duration,"media_url":f"/api/media/{media.id}"}
