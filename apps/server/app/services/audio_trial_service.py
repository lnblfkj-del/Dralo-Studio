"""Model-local audio tests reuse durable audio jobs without creating a project."""

# ruff: noqa: RUF001
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.media_quota import require_media_budget
from app.core.storage_safety import write_storage_bytes
from app.models import Job, MediaFile, Provider, ProviderModel
from app.providers.audio_contracts import music_parameters
from app.providers.protocols import effective_protocol, execution_contract, model_confirmation_token
from app.providers.speech import speech_parameters
from app.services import canvas_generation_service, job_service, pricing_service, provider_service
from app.services.planning_capability_admin import lock_catalog

TARGET = "model_audio_test"


class TrialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=10000)
    voice: str | None = Field(default=None, max_length=255)
    duration: float | None = Field(default=None, ge=3, le=600, allow_inf_nan=False)
    instrumental: bool | None = Field(default=None, strict=True)
    lyrics: str | None = Field(default=None, max_length=4096)
    voice_description: str | None = Field(default=None, min_length=1, max_length=1000)
    design_model: Literal["eleven_multilingual_ttv_v2", "eleven_ttv_v3"] | None = None

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value):
        if not value.strip():
            raise ValueError("请填写生成文本")
        return value.strip()


class TrialStart(TrialInput):
    request_id: UUID
    confirmation_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    max_cost_cents: int = Field(default=0, ge=0)
    accept_unknown_price: bool = False


def digest(value):
    return canvas_generation_service.fingerprint(value)


async def validate_model(session, model_id, parameters):
    model = await session.get(ProviderModel, model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if not model or not provider or model.model_type != "tts":
        return await canvas_generation_service.validate_model(session, model_id, "audio", parameters)
    from app.providers.protocols import validate_model_protocol
    from app.services.audio_verification_service import require_ready
    from app.services.audio_voice_catalog import read
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    validate_model_protocol(provider, model)
    if effective_protocol(provider, model) == "stepfun_gen" or parameters.get("design_model"):
        if parameters.get("design_model") and effective_protocol(provider, model) != "elevenlabs_tts":
            raise ConflictError("音色设计仅适用于 ElevenLabs 原生配音渠道")
        require_ready(provider, model, parameters, require_voice=False)
        return model
    voice = parameters.get("voice", model.default_params.get("voice"))
    catalog = read(provider, model)
    registered = model.default_params.get("voices", [])
    registered = registered if isinstance(registered, list) else []
    if voice not in registered and not any(row["id"] == voice for row in catalog.get("items", [])):
        raise ConflictError("音色未登记或不属于当前账户目录，请重新选择")
    # A cached-directory preview never edits the model's registered/default voice.
    trial = SimpleNamespace(**{column.name: getattr(model, column.name) for column in model.__table__.columns})
    trial.default_params = {**model.default_params, "voices": [voice], "voice": voice}
    require_ready(provider, trial, parameters)
    return trial


async def plan(session, provider_id, model_id, payload):
    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    parameters = {}
    if payload.voice_description is not None:
        parameters["voice_description"] = payload.voice_description
    if payload.design_model is not None:
        parameters["design_model"] = payload.design_model
    if payload.voice is not None:
        parameters["voice"] = payload.voice
    if payload.duration is not None:
        parameters["duration"] = payload.duration
    if payload.instrumental is not None:
        parameters["instrumental"] = payload.instrumental
    if payload.lyrics is not None:
        parameters["lyrics"] = payload.lyrics
    trial_model = await validate_model(session, model.id, parameters)
    protocol = effective_protocol(provider, model)
    if model.model_type == "tts":
        if payload.instrumental is not None or payload.lyrics is not None:
            raise ConflictError("配音不使用歌曲参数")
        if payload.duration is not None:
            raise ConflictError("配音无需填写音乐时长")
        if payload.design_model:
            if protocol != "elevenlabs_tts" or payload.voice is not None:
                raise ConflictError("音色设计不使用预设音色")
            if not payload.voice_description or not 20 <= len(payload.voice_description.strip()) <= 1000 or not 100 <= len(payload.prompt) <= 1000:
                raise ConflictError("ElevenLabs 声线描述需 20–1000 字符，试听文本需 100–1000 字符")
        else:
            if payload.voice_description is not None and protocol != "stepfun_gen":
                raise ConflictError("请切换为参考声线生成，指定音色配音不使用声线描述")
            parameters = speech_parameters(trial_model, parameters, payload.prompt, protocol=protocol, provider=provider)
    else:
        if payload.voice_description is not None or payload.design_model is not None:
            raise ConflictError("音乐测试不使用参考声线参数")
        if payload.voice is not None:
            raise ConflictError("配乐不使用配音音色")
        parameters = music_parameters(model, parameters, payload.prompt, protocol)
    quote_parameters = {**parameters, **({"duration": payload.duration} if payload.duration is not None else {})}
    quote = pricing_service.estimate(model, payload.prompt, quote_parameters)
    if payload.design_model:
        quote = {**quote, "status": "unknown", "amount": None, "estimated_cents": None,
                 "reason": "音色设计不使用配音单价，费用以渠道账单为准"}
    token = digest([await model_confirmation_token(session, model), payload.model_dump(), parameters, quote])
    return {"confirmation_token": token, "parameters": parameters, "quote": quote, "model_called": False}


async def start(session, owner_id, provider_id, model_id, payload):
    provider, model = await lock_catalog(session, provider_id, model_id)
    request = TrialInput.model_validate(payload.model_dump(include=set(TrialInput.model_fields)))
    request_hash = digest([request.model_dump(), payload.confirmation_token])
    prior = await session.scalar(select(Job).where(Job.owner_id == owner_id,
        Job.target_type == TARGET, Job.target_id == model_id,
        Job.payload["parameters"]["audio_request_id"].as_string() == str(payload.request_id)))
    if prior:
        if prior.payload["parameters"]["audio_request_digest"] != request_hash:
            raise ConflictError("请求编号已用于其他测试，请重新确认")
        return prior
    checked = await plan(session, provider_id, model_id, request)
    if checked["confirmation_token"] != payload.confirmation_token:
        raise ConflictError("模型、音色或费用已变化，请重新确认测试")
    cost = checked["quote"]["estimated_cents"]
    if cost is None and not payload.accept_unknown_price:
        raise ConflictError("价格未知不等于免费，请确认未知费用")
    if cost is not None and cost > payload.max_cost_cents:
        raise ConflictError("费用超过已确认金额，请重新确认")
    from app.services.canvas_processing_service import executables
    executables()
    # Store only user parameters here; the worker compiles the native request again.
    parameters = {key: value for key, value in {"voice": payload.voice, "duration": payload.duration,
        "instrumental": payload.instrumental, "lyrics": payload.lyrics,
        "voice_description": payload.voice_description, "design_model": payload.design_model}.items() if value is not None}
    if "voice" in checked["parameters"]:
        parameters["voice"] = checked["parameters"]["voice"]
    trial_model = await validate_model(session, model.id, parameters)
    job = Job(owner_id=owner_id, provider_id=provider.id, project_id=None,
        job_type="tts" if model.model_type == "tts" else "audio", target_type=TARGET, target_id=model.id,
        status="queued", max_attempts=1, provider=provider.name, model=model.model_id,
        payload={"operation": "audio_trial", "provider_model_id": model.id, "prompt": payload.prompt,
            "protocol_contract": execution_contract(provider, trial_model), "parameters": {**parameters,
                "audio_request_id": str(payload.request_id), "audio_request_digest": request_hash}})
    pricing_service.attach(job, model)
    if payload.design_model:
        from app.services.audio_verification_service import check_fingerprint
        job.cost_estimate = None
        job.payload = {**job.payload, "operation": "voice_design", "prompt": payload.voice_description.strip(),
            "voice_scope": check_fingerprint(provider, model),
            "parameters": {**job.payload["parameters"], "text": payload.prompt},
            "pricing_snapshot": {**checked["quote"], "rates": {}}}
    session.add(job)
    await session.flush()
    return job


async def history(session, owner_id, provider_id, model_id):
    await provider_service.get_model(session, provider_id, model_id)
    return (await session.scalars(select(Job).where(Job.owner_id == owner_id, Job.provider_id == provider_id,
        Job.target_type == TARGET, Job.target_id == model_id, Job.deleted_at.is_(None))
        .order_by(Job.id.desc()).limit(20))).all()


async def clear_history(session, owner_id, provider_id, model_id):
    await provider_service.get_model(session, provider_id, model_id)
    rows = (await session.scalars(select(Job).where(
        Job.owner_id == owner_id, Job.provider_id == provider_id,
        Job.project_id.is_(None), Job.target_type == TARGET, Job.target_id == model_id,
        Job.status.in_(job_service.TERMINAL_STATUSES),
    ))).all()
    from app.services.audio_trial_cleanup import cleanup
    deleted_ids, deleted_media_ids, retained_media_ids = [], [], []
    for job in rows:
        if job.payload.get("audio_test_cleared"):
            continue
        removed, retained = await cleanup(session, job)
        deleted_media_ids.extend(removed)
        retained_media_ids.extend(retained)
        if job.deleted_at is None:
            await job_service.delete_job(session, job)
        deleted_ids.append(job.id)
    return {"deleted_ids": deleted_ids, "deleted_media_ids": deleted_media_ids, "retained_media_ids": retained_media_ids}


async def finalize(session, job, result):
    path = Path("model-tests") / str(job.owner_id) / f"audio-{job.id}.mp3"
    prior = await session.scalar(select(MediaFile).where(MediaFile.owner_id == job.owner_id,
        MediaFile.project_id.is_(None), MediaFile.file_path == path.as_posix()))
    if prior:
        return {"media_file_id": prior.id, "duration": prior.duration}
    data = result["audio_bytes"]
    await require_media_budget(session, len(data))
    write_storage_bytes(settings, settings.storage_path / path, data)
    media = MediaFile(owner_id=job.owner_id, project_id=None, purpose="model_test", kind="audio", source="generation",
        file_path=path.as_posix(), original_name=f"audio-test-{job.id}.mp3", mime_type="audio/mpeg",
        size=len(data), hash=sha256(data).hexdigest(), duration=result["duration"])
    session.add(media)
    await session.flush()
    return {"media_file_id": media.id, "duration": media.duration}
