"""Freeze video routes without putting credentials in jobs or exported packages."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.provider_crypto import encrypt_api_key, api_key_hint
from app.models import ProviderModel, VideoAccount, VideoExecutionVersion

PROVIDER_FIELDS = ("id", "name", "protocol", "base_url", "timeout_seconds", "proxy_url", "max_concurrency")
MODEL_FIELDS = ("id", "provider_id", "model_id", "name", "model_type", "api_protocol", "api_base_url",
                "capabilities", "default_params", "video_config", "video_prompt_certifications", "pricing",
                "max_concurrency")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def current_account(session, provider):
    from sqlalchemy import update
    from app.models import Provider
    await session.execute(update(Provider).where(Provider.id == provider.id).values(
        name=Provider.name, updated_at=Provider.updated_at).execution_options(synchronize_session=False))
    account = await session.scalar(select(VideoAccount).where(
        VideoAccount.provider_id == provider.id, VideoAccount.current.is_(True)))
    if account is None:
        account = VideoAccount(provider_id=provider.id, workspace_id=provider.workspace_id, current=True,
                               key_ciphertext=provider.api_key_ciphertext, key_hint=provider.api_key_hint)
        session.add(account)
        await session.flush()
    return account


async def capture(session, provider, model):
    from app.services.video_adapter_installation import require_installed
    await require_installed(session, model)
    account = await current_account(session, provider)
    proxy = provider.proxy_url
    if proxy and urlsplit(proxy).username:
        raise ConflictError("视频版本不保存代理密码，请使用不含账号密码的本地代理地址")
    snapshot = {"provider": {key: deepcopy(getattr(provider, key)) for key in PROVIDER_FIELDS},
                "model": {key: deepcopy(getattr(model, key)) for key in MODEL_FIELDS},
                "account_id": account.id}
    fingerprint = digest(snapshot)
    version = await session.scalar(select(VideoExecutionVersion).where(
        VideoExecutionVersion.model_id == model.id, VideoExecutionVersion.fingerprint == fingerprint))
    if version is None:
        version = VideoExecutionVersion(model_id=model.id, account_id=account.id,
            fingerprint=fingerprint, snapshot=snapshot, workspace_id=model.workspace_id)
        session.add(version)
        await session.flush()
    return version


async def bind(session, job, provider, model):
    version = await capture(session, provider, model)
    job.payload = {**job.payload, "video_execution_version": {
        "id": version.id, "fingerprint": version.fingerprint, "account_id": version.account_id}}


async def resolve(session, job, provider, model):
    marker = (job.payload or {}).get("video_execution_version")
    if marker is None:
        return provider, model
    if not isinstance(marker, dict):
        raise ConflictError("视频执行版本引用损坏，已停止请求")
    version = await session.get(VideoExecutionVersion, marker.get("id"))
    if (version is None or version.model_id != model.id or version.fingerprint != marker.get("fingerprint")
            or version.account_id != marker.get("account_id") or digest(version.snapshot) != version.fingerprint):
        raise ConflictError("视频执行版本不存在或已变化，禁止改用当前接法重试")
    account = await session.get(VideoAccount, version.account_id)
    if account is None or account.provider_id != provider.id:
        raise ConflictError("原视频账号不存在，请恢复该账号；不会使用新账号代替")
    return (SimpleNamespace(**deepcopy(version.snapshot["provider"]), enabled=provider.enabled,
                            api_key_ciphertext=account.key_ciphertext, workspace_id=provider.workspace_id),
            SimpleNamespace(**deepcopy(version.snapshot["model"]), enabled=model.enabled,
                            workspace_id=model.workspace_id))


async def remember_models(session, provider):
    for model in provider.models:
        await session.refresh(model)
        if model.model_type == "video":
            await capture(session, provider, model)


async def change_key(session, provider, key, action):
    account = await current_account(session, provider)
    cipher, hint = encrypt_api_key(key), api_key_hint(key)
    if action == "rotate":
        account.key_ciphertext, account.key_hint = cipher, hint
    else:
        account.current = False
        await session.flush()
        session.add(VideoAccount(provider_id=provider.id, workspace_id=provider.workspace_id,
            current=True, key_ciphertext=cipher, key_hint=hint))
    provider.api_key_ciphertext, provider.api_key_hint = cipher, hint
    await session.flush()


async def history(session, provider, model):
    from app.services.planning_capability_admin import config_fingerprint
    # Reading history is read-only; versions are recorded on save or job creation.
    rows = list(await session.scalars(select(VideoExecutionVersion).where(
        VideoExecutionVersion.model_id == model.id).order_by(VideoExecutionVersion.id.desc()).limit(100)))
    current = {"provider": {key: getattr(provider, key) for key in PROVIDER_FIELDS},
               "model": {key: getattr(model, key) for key in MODEL_FIELDS}}
    account_id = await session.scalar(select(VideoAccount.id).where(
        VideoAccount.provider_id == provider.id, VideoAccount.current.is_(True)))
    return {"expected_fingerprint": config_fingerprint(provider, model), "items": [
        {"id": row.id, "created_at": row.created_at, "account_id": row.account_id,
         "fingerprint": row.fingerprint, "protocol": row.snapshot["model"]["api_protocol"] or row.snapshot["provider"]["protocol"],
         "model_identifier": row.snapshot["model"]["model_id"],
         "current": row.account_id == account_id and all(row.snapshot[key] == current[key] for key in current)} for row in rows]}


async def rollback(session, provider_id, model_id, version_id, expected):
    from app.services.planning_capability_admin import lock_catalog, config_fingerprint, assert_no_active_planning
    from app.services.video_configuration import read, save_input
    from app.providers.protocols import effective_base_url, effective_protocol
    provider, model = await lock_catalog(session, provider_id, model_id)
    if config_fingerprint(provider, model) != expected:
        raise ConflictError("配置已变化，请刷新后重新选择回退版本")
    version = await session.get(VideoExecutionVersion, version_id)
    if version is None or version.model_id != model.id:
        raise ConflictError("该版本不属于当前模型")
    await assert_no_active_planning(session, provider, model)
    await capture(session, provider, model)
    old = version.snapshot
    old_provider, old_model = SimpleNamespace(**old["provider"]), SimpleNamespace(**old["model"])
    config = read(old_model)
    if config is None or config.specification is None:
        raise ConflictError("该历史版本规格不完整，请先补齐配置，不能直接回退")
    # Account identity and billing settings are deliberately not rolled back.
    model.api_protocol = effective_protocol(old_provider, old_model)
    model.api_base_url = effective_base_url(old_provider, old_model)
    if model.model_id != old_model.model_id:
        raise ConflictError("历史模型名称不同，请另建模型；不会把当前模型改成另一模型")
    save_input(provider, model, {key: config.model_dump(mode="json")[key] for key in (
        "schema_version", "integration", "specification", "generation_defaults", "planning_policy", "transport_params")})
    await capture(session, provider, model)
    await session.flush()
    return await history(session, provider, model)


async def assert_deletable(session, provider_id, model_id=None):
    from app.models import Job
    from sqlalchemy import delete
    query = select(VideoExecutionVersion.id).join(ProviderModel, ProviderModel.id == VideoExecutionVersion.model_id).where(
        ProviderModel.provider_id == provider_id)
    if model_id is not None:
        query = query.where(VideoExecutionVersion.model_id == model_id)
    ids = list(await session.scalars(query))
    referenced = await session.scalar(select(Job.id).where(
        Job.provider_id == provider_id, Job.payload["video_execution_version"]["id"].as_integer().in_(ids)).limit(1)) if ids else None
    if referenced:
        raise ConflictError("此模型或渠道已有视频执行历史，请停用而非删除，以保留旧任务恢复能力")
    if ids:
        await session.execute(delete(VideoExecutionVersion).where(VideoExecutionVersion.id.in_(ids)))
    if model_id is None:
        await session.execute(delete(VideoAccount).where(VideoAccount.provider_id == provider_id))
