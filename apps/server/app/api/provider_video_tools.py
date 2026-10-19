"""Model-local video tools; only the explicitly confirmed start creates a job."""

from fastapi import APIRouter
from fastapi.responses import Response
import json

from app.api.deps import AdminUser, SessionDep
from app.schemas.job import JobOut
from app.schemas.video_tools import PackageLink, VideoCheckInput, VideoPackage, VideoTrialInput, VideoTrialStart
from app.schemas.video_tools import VideoRollback, VideoAdapterInstall, VideoAdapterState, VideoKeyRotation
from app.schemas.video_tools import VideoPresetCreate, VideoPresetPreview
from app.services import provider_service
from app.services import video_tools_service as service

router = APIRouter()


@router.get("/video-config/assistant")
async def import_assistance(_admin: AdminUser):
    from app.services.video_import_assistance import assistance

    return assistance()


@router.get("/video-config/catalog")
async def configuration_catalog(session: SessionDep, _admin: AdminUser):
    from app.services.video_preset_catalog import catalog
    return await catalog(session)


@router.post("/video-config/catalog/preview")
async def preview_configuration(payload: VideoPresetPreview,
                                session: SessionDep, _admin: AdminUser):
    from app.services.video_preset_catalog import preview
    return await preview(session, payload)


@router.post("/video-config/catalog", status_code=201)
async def save_configuration(payload: VideoPresetCreate,
                             session: SessionDep, _admin: AdminUser):
    from app.services.video_preset_catalog import save
    result = await save(session, payload)
    await session.commit()
    return result


@router.get("/video-config/template/{kind}")
async def download_template(kind: str, _admin: AdminUser):
    from app.core.errors import NotFoundError
    from app.services.video_package_templates import example
    if kind not in {"json", "service"}:
        raise NotFoundError("模板不存在")
    package = service.validate_package(example(service=kind == "service"))
    return Response(json.dumps(package, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="video-{kind}-template.json"'})


@router.get("/video-adapters")
async def adapters(session: SessionDep, _admin: AdminUser):
    from sqlalchemy import select
    from app.models import VideoAdapterRelease
    return [{"id": row.id, "name": row.package["name"], "adapter_key": row.adapter_key,
             "version": row.version, "enabled": row.enabled, "fingerprint": row.fingerprint}
            for row in await session.scalars(select(VideoAdapterRelease).order_by(VideoAdapterRelease.id.desc()))]


@router.post("/{provider_id}/video-adapters/install")
async def install_adapter(provider_id: int, payload: VideoAdapterInstall, session: SessionDep, _admin: AdminUser):
    from app.services.video_adapter_installation import install
    provider = await provider_service.get_provider(session, provider_id)
    result = await install(session, payload.package.model_dump(mode="json"), provider)
    await session.commit()
    return result


@router.patch("/video-adapters/{release_id}")
async def adapter_state(release_id: int, payload: VideoAdapterState, session: SessionDep, _admin: AdminUser):
    from app.models import VideoAdapterRelease
    from app.core.errors import NotFoundError
    row = await session.get(VideoAdapterRelease, release_id)
    if row is None:
        raise NotFoundError("适配服务不存在")
    row.enabled = payload.enabled
    await session.commit()
    return {"id": row.id, "enabled": row.enabled}


@router.get("/{provider_id}/video-accounts")
async def accounts(provider_id: int, session: SessionDep, _admin: AdminUser):
    from sqlalchemy import select
    from app.models import VideoAccount
    await provider_service.get_provider(session, provider_id)
    return [{"id": row.id, "current": row.current, "key_hint": row.key_hint, "created_at": row.created_at}
            for row in await session.scalars(select(VideoAccount).where(VideoAccount.provider_id == provider_id).order_by(VideoAccount.id.desc()))]


@router.post("/{provider_id}/video-accounts/{account_id}/rotate-key")
async def rotate_key(provider_id: int, account_id: int, payload: VideoKeyRotation, session: SessionDep, _admin: AdminUser):
    from app.core.errors import NotFoundError, ConflictError
    from app.core.provider_crypto import encrypt_api_key, api_key_hint, decrypt_api_key
    from app.models import VideoAccount
    from sqlalchemy import update
    from app.models import Provider
    await session.execute(update(Provider).where(Provider.id == provider_id).values(name=Provider.name, updated_at=Provider.updated_at))
    provider = await provider_service.get_provider(session, provider_id)
    account = await session.get(VideoAccount, account_id)
    if account is None or account.provider_id != provider.id:
        raise NotFoundError("视频账号不存在")
    # Historical accounts may use a different protocol from the channel's current one.
    if not decrypt_api_key(account.key_ciphertext):
        raise ConflictError("原账号凭据损坏，不能用新账号替代")
    account.key_ciphertext, account.key_hint = encrypt_api_key(payload.api_key), api_key_hint(payload.api_key)
    if account.current:
        provider.api_key_ciphertext, provider.api_key_hint = account.key_ciphertext, account.key_hint
    await session.commit()
    return {"id": account.id, "current": account.current, "key_hint": account.key_hint}


@router.get("/{provider_id}/models/{model_id}/video-versions")
async def version_history(provider_id: int, model_id: int, session: SessionDep, _admin: AdminUser):
    from app.services.video_version_service import history
    return await history(session, *await model_pair(session, provider_id, model_id))


@router.post("/{provider_id}/models/{model_id}/video-versions/rollback")
async def rollback_version(provider_id: int, model_id: int, payload: VideoRollback, session: SessionDep, _admin: AdminUser):
    from app.services.video_version_service import rollback
    result = await rollback(session, provider_id, model_id, payload.version_id, payload.expected_fingerprint)
    await session.commit()
    return result


@router.get("/video-config/templates")
async def templates(_admin: AdminUser):
    from app.providers.protocols import PROTOCOL_CATALOG

    return {"items": [item for item in PROTOCOL_CATALOG if "video" in item["model_types"]],
            "custom_example": {"name": "JSON异步视频", "submit_path": "/render/tasks",
                "request": {"model": "$model", "prompt": "$prompt", "seconds": "$duration",
                            "resolution": "$resolution", "ratio": "$aspect_ratio",
                            "first_image": "$first_frame", "last_image": "$last_frame", "images": "$reference_images"},
                "task_id_path": "data.task_id", "poll_path": "/render/tasks/{task_id}",
                "status_path": "data.status", "succeeded": ["done"], "failed": ["failed", "cancelled"],
                "processing": ["queued", "processing"], "result_url_path": "data.video_url"}}


@router.post("/video-config/import")
async def import_package(payload: VideoPackage, _admin: AdminUser):
    return service.validate_package(payload.model_dump(mode="json"))


@router.post("/video-config/import-link")
async def import_package_link(payload: PackageLink, _admin: AdminUser):
    return await service.import_link(payload.url)


async def model_pair(session, provider_id, model_id):
    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    return provider, model


@router.get("/{provider_id}/models/{model_id}/video-config/export")
async def export_package(provider_id: int, model_id: int, session: SessionDep, _admin: AdminUser):
    return service.export_package(*await model_pair(session, provider_id, model_id))


@router.post("/{provider_id}/models/{model_id}/video-check")
async def check_config(provider_id: int, model_id: int, payload: VideoCheckInput, session: SessionDep, _admin: AdminUser):
    return service.check(*await model_pair(session, provider_id, model_id), payload)


@router.post("/{provider_id}/models/{model_id}/video-test/plan")
async def plan_trial(provider_id: int, model_id: int, payload: VideoTrialInput, session: SessionDep, admin: AdminUser):
    provider, model = await model_pair(session, provider_id, model_id)
    return await service.trial_plan(session, admin.id, provider, model, payload)


@router.post("/{provider_id}/models/{model_id}/video-test/start", response_model=JobOut)
async def start_trial(provider_id: int, model_id: int, payload: VideoTrialStart, session: SessionDep, admin: AdminUser):
    job = await service.start_trial(session, admin.id, provider_id, model_id, payload)
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/{provider_id}/models/{model_id}/video-test/history")
async def history(provider_id: int, model_id: int, session: SessionDep, admin: AdminUser):
    await model_pair(session, provider_id, model_id)
    return await service.trial_history(session, admin.id, model_id)
