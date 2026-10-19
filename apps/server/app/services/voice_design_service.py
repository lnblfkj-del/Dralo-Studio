"""Project-scoped voice design candidates and durable, explicit identity adoption."""

# ruff: noqa: RUF001
import base64
import hashlib
import json
from functools import partial
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError, GenerationFailedError
from app.core.media_quota import require_media_budget
from app.core.provider_crypto import decrypt_api_key
from app.core.storage_safety import finish_storage_io, write_storage_bytes
from app.models import Job, MediaFile, ProjectMediaLink, Provider, ProviderModel
from app.providers.audio_transport import NativeAudioProvider, mp3_result
from app.providers.protocols import effective_base_url, effective_protocol, validate_model_protocol
from app.services import (
    asset_production_service,
    asset_service,
    canvas_generation_service,
    job_service,
)
from app.services.asset_audio_profile import effective_profile
from app.services.audio_verification_service import check_fingerprint


def is_design(job):
    return (job.payload or {}).get("operation") == "voice_design"


async def target(session, project_id, asset_id):
    asset = await asset_service.get_asset(session, project_id, asset_id)
    link = await asset_production_service.ensure_active_link(session, project_id, asset_id)
    if asset.project_id != project_id or not (asset.asset_type == "character" or
            (asset.asset_type == "voice" and effective_profile(asset, link.production_data).get("audio_usage") == "voice")):
        raise ConflictError("音色设计仅用于当前项目的角色或配音资产")
    return asset, link


async def design_model(session, model_id, scope=None):
    model = await session.get(ProviderModel, model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if not model or not provider or not provider.enabled or not model.enabled:
        raise ConflictError("音色设计模型或渠道不可用")
    if model.model_type != "tts" or effective_protocol(provider, model) != "elevenlabs_tts" or "speech" not in model.capabilities:
        raise ConflictError("当前仅 ElevenLabs 原生配音协议支持描述设计音色")
    validate_model_protocol(provider, model)
    if scope and scope != check_fingerprint(provider, model):
        raise ConflictError("音色设计的渠道账户已变化，不能跨账户恢复或采用")
    return provider, model


def transport(provider, model):
    return NativeAudioProvider(protocol="elevenlabs_tts", base_url=effective_base_url(provider, model),
        api_key=decrypt_api_key(provider.api_key_ciphertext), timeout_seconds=provider.timeout_seconds, proxy_url=provider.proxy_url)


async def validate_target(session, job):
    if job.target_type == "model_audio_test":
        if job.project_id is not None or job.target_id != job.payload.get("provider_model_id") or not is_design(job):
            raise ConflictError("音色测试目标不匹配")
        return
    await target(session, job.project_id, job.target_id)


async def latest(session, project, asset_id):
    await target(session, project.id, asset_id)
    return await session.scalar(select(Job).where(Job.project_id == project.id, Job.target_type == "asset",
        Job.target_id == asset_id, Job.payload["operation"].as_string() == "voice_design", Job.deleted_at.is_(None))
        .order_by(Job.id.desc()).limit(1))


async def submit(session, project, asset_id, payload):
    await canvas_generation_service.submission_lock(session, project)
    digest = canvas_generation_service.fingerprint([asset_id, payload.model_dump(mode="json", exclude={"request_id"})])
    prior = await session.scalar(select(Job).where(Job.project_id == project.id,
        Job.payload["parameters"]["voice_design_request_id"].as_string() == str(payload.request_id)))
    if prior:
        if prior.payload["parameters"].get("request_digest") != digest:
            raise ConflictError("请求编号已用于其他音色设计")
        return prior
    _asset, link = await target(session, project.id, asset_id)
    if link.production_revision != payload.expected_revision:
        raise ConflictError("资产资料已变化，请刷新后再确认生成")
    active = await session.scalar(select(Job.id).where(Job.project_id == project.id, Job.target_type == "asset",
        Job.target_id == asset_id, Job.status.not_in(job_service.TERMINAL_STATUSES)))
    if active:
        raise ConflictError("该资产已有进行中的任务")
    provider, model = await design_model(session, payload.provider_model_id)
    from app.services.canvas_processing_service import executables
    executables()
    job = Job(owner_id=project.owner_id, project_id=project.id, provider_id=provider.id,
        job_type="tts", target_type="asset", target_id=asset_id, status="queued", provider=provider.name, model=model.model_id,
        payload={"operation":"voice_design", "provider_model_id":model.id, "prompt":payload.description,
            "voice_scope":check_fingerprint(provider, model), "protocol_contract":{"voice_design_version":1},
            "audio_purpose":"voice_reference",
            "parameters":{"text":payload.text, "design_model":payload.design_model,
                "voice_design_request_id":str(payload.request_id), "request_digest":digest},
            "pricing_snapshot":{"status":"unknown", "amount":None, "estimated_cents":None, "currency":"CNY",
                "rates":{}, "reason":"音色设计不使用配音单价，实际费用以渠道账单为准"}})
    session.add(job)
    await session.flush()
    return job


def receipt(job, *, adoption=False):
    from app.jobs.audio_task import fingerprint, receipt_path
    path = receipt_path(job)
    if path is None:
        raise GenerationFailedError("音色设计缺少本地回执标识")
    return path.with_suffix(".adopt.json") if adoption else path, fingerprint(job)


def save_receipt(job, result, *, adoption=False):
    path, signature = receipt(job, adoption=adoption)
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    envelope = {"fingerprint":signature, "sha256":hashlib.sha256(body).hexdigest(), "body":result}
    write_storage_bytes(settings, path, json.dumps(envelope, ensure_ascii=False).encode())


def load_receipt(job, *, adoption=False):
    path, signature = receipt(job, adoption=adoption)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 110 * 1024 * 1024:
        raise GenerationFailedError("音色设计回执异常，禁止重新调用模型")
    data = json.loads(path.read_bytes())
    body = json.dumps(data["body"], ensure_ascii=False, separators=(",", ":")).encode()
    if data["fingerprint"] != signature or data["sha256"] != hashlib.sha256(body).hexdigest():
        raise GenerationFailedError("音色设计回执不匹配，禁止重新调用模型")
    return data["body"]


def previews(result):
    items = result.get("previews") if isinstance(result, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise GenerationFailedError("渠道没有返回有效音色候选")
    ids = set()
    for item in items:
        voice_id = item.get("generated_voice_id") if isinstance(item, dict) else None
        if not isinstance(voice_id, str) or not voice_id.strip() or len(voice_id) > 255 or voice_id in ids:
            raise GenerationFailedError("渠道音色候选标识无效")
        ids.add(voice_id)
        if item.get("media_type") not in {"audio/mpeg", "audio/mp3"}:
            raise GenerationFailedError("渠道音色候选不是 MP3")
        try:
            mp3_result(base64.b64decode(item["audio_base_64"], validate=True))
        except (ValueError, TypeError, KeyError) as exc:
            raise GenerationFailedError("渠道试听音频无效") from exc
    return items


async def persist(session, job, result):
    await validate_target(session, job)
    trial = job.target_type == "model_audio_test"
    from app.services.generated_audio_media import inspect_audio
    rows = []
    for index, item in enumerate(previews(result)):
        root = Path("model-tests") / str(job.owner_id) if trial else Path("projects") / str(job.project_id)
        relative = root / "voice-design" / f"{job.id}-{index}.mp3"
        media = await session.scalar(select(MediaFile).where(MediaFile.owner_id == job.owner_id, MediaFile.project_id == job.project_id, MediaFile.file_path == relative.as_posix()))
        if media is None:
            audio = base64.b64decode(item["audio_base_64"], validate=True)
            metadata = await inspect_audio(audio)
            await require_media_budget(session, len(audio))
            write_storage_bytes(settings, settings.storage_path / relative, audio)
            media = MediaFile(owner_id=job.owner_id, project_id=job.project_id, kind="audio", source="generation", purpose="model_test" if trial else "voice_reference",
                file_path=relative.as_posix(), original_name=f"音色候选-{index+1}.mp3", mime_type="audio/mpeg",
                size=len(audio), hash=hashlib.sha256(audio).hexdigest(), duration=metadata["duration"])
            session.add(media)
            await session.flush()
            if not trial:
                session.add(ProjectMediaLink(project_id=job.project_id, media_file_id=media.id))
        rows.append({"index":index, "generated_voice_id":item["generated_voice_id"], "media_file_id":media.id,
            "language":str(item.get("language") or "")[:80], "duration":media.duration})
        if job.payload.get("audio_purpose") == "voice_reference":
            from app.models import Asset, AssetVersion
            asset = await session.get(Asset, job.target_id)
            if asset.asset_type == "voice":
                version = await session.scalar(select(AssetVersion).where(AssetVersion.asset_id == asset.id, AssetVersion.media_file_id == media.id))
                if version is None:
                    from sqlalchemy import func
                    number = int(await session.scalar(select(func.coalesce(func.max(AssetVersion.version), 0)).where(AssetVersion.asset_id == asset.id))) + 1
                    version = AssetVersion(asset_id=asset.id, media_file_id=media.id, source_job_id=job.id, version=number,
                        prompt=job.payload["parameters"]["text"], parameters={"audio_purpose":"voice_reference", "voice_description":job.payload["prompt"], "voice_sample_method":"voice_design"},
                        view_type="base", view_label=f"参考声线候选 {index+1}", review_status="candidate", is_final=False)
                    session.add(version)
                    await session.flush()
                rows[-1]["asset_version_id"] = version.id
    return {"previews":rows, "text":str(result.get("text") or job.payload["parameters"]["text"])[:1000]}


async def adopt(session, project, asset_id, job_id, payload):
    from app.services.planning_capability_admin import lock_catalog
    from app.services.provider_service import assert_no_active_jobs

    await canvas_generation_service.submission_lock(session, project)
    await target(session, project.id, asset_id)
    job = await session.get(Job, job_id, populate_existing=True)
    if not job or job.project_id != project.id or job.target_id != asset_id or not is_design(job) or job.status != "succeeded":
        raise ConflictError("音色候选不存在或尚未完成")
    provider, model = await design_model(session, job.payload["provider_model_id"], job.payload["voice_scope"])
    provider, model = await lock_catalog(session, provider.id, model.id)
    await design_model(session, model.id, job.payload["voice_scope"])
    rows = (job.result or {}).get("previews", [])
    if payload.preview_index >= len(rows):
        raise ConflictError("音色候选不存在")
    prior = job.payload.get("voice_adoption") or {}
    if prior and (prior["index"] != payload.preview_index or prior["name"] != payload.name):
        raise ConflictError("该批音色已开始采用其他候选，请勿重复创建")
    result = prior if prior.get("voice_id") else load_receipt(job, adoption=True)
    if result is None:
        if prior.get("started"):
            raise ConflictError("采用结果待核对，禁止自动重发；请在渠道检查音色目录后手动绑定")
        voices = list((model.default_params or {}).get("voices") or [])
        if len(voices) >= 100:
            raise ConflictError("已登记 100 个音色，请先整理模型音色清单")
        await assert_no_active_jobs(session, provider.id, model.id)
        job.payload = {**job.payload, "voice_adoption":{"started":True, "index":payload.preview_index, "name":payload.name}}
        await session.commit()
        # Durable intent is committed before the non-idempotent create-voice call.
        result = await transport(provider, model)._post("/text-to-voice", {"voice_name":payload.name,
            "voice_description":job.payload["prompt"], "generated_voice_id":rows[payload.preview_index]["generated_voice_id"]}, json_response=True)
        voice_id = result.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id.strip() or len(voice_id) > 255:
            raise GenerationFailedError("渠道未返回可复用音色 ID，请核对渠道音色目录")
        result = {"voice_id":voice_id, "index":payload.preview_index, "name":payload.name}
        await finish_storage_io(partial(save_receipt, adoption=True), job, result)
    await canvas_generation_service.submission_lock(session, project)
    provider, model = await lock_catalog(session, provider.id, model.id)
    await design_model(session, model.id, job.payload["voice_scope"])
    await target(session, project.id, asset_id)
    voices = list((model.default_params or {}).get("voices") or [])
    if result["voice_id"] not in voices:
        if len(voices) >= 100:
            raise ConflictError("已登记 100 个音色；渠道音色已创建，整理清单后可重试登记，不再次创建")
        # Adding an ID changes legacy frozen TTS contracts. Wait rather than
        # invalidating any queued calls that arrived during upstream adoption.
        await assert_no_active_jobs(session, provider.id, model.id)
        voices.append(result["voice_id"])
    model.default_params = {**model.default_params, "voices":voices,
        "_audio_voice_names":{**model.default_params.get("_audio_voice_names", {}), result["voice_id"]:payload.name}}
    job.payload = {**job.payload, "voice_adoption":{**result, "started":True}}
    binding = {"provider_model_id":model.id, "voice":result["voice_id"], "account_fingerprint":job.payload["voice_scope"]}
    job.result = {**job.result, "adopted":{"index":payload.preview_index, "name":payload.name, "binding":binding}}
    await session.flush()
    return binding
