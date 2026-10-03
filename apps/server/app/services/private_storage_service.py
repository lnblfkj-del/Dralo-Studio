"""Cloud account storage versions. Secrets never enter task snapshots or responses."""

import asyncio
import json
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.provider_crypto import decrypt_api_key
from app.models import MediaFile, User
from app.models.base import utcnow
from app.models.object_storage import ObjectStorageVersion, UserObjectStorage
from app.services import storage_service as storage


def defaults():
    return storage.StorageUpdate(revision=0).model_dump(
        exclude={"access_key", "secret_key", "clear_credentials", "local_path"}
    )


def missing(message):
    return ConflictError(
        message, details={"settings_url": "/settings/storage", "reason": "object_storage_required"}
    )


async def current_config(session, owner_id):
    profile = await session.get(UserObjectStorage, owner_id)
    if profile is None or profile.version_id is None:
        return defaults()
    version = await session.scalar(
        select(ObjectStorageVersion).where(
            ObjectStorageVersion.id == profile.version_id, ObjectStorageVersion.owner_id == owner_id
        )
    )
    if version is None or version.revoked:
        raise missing("个人对象存储配置不可用，请重新保存存储设置")
    return dict(version.config)


async def public_config(session, owner_id):
    value = await current_config(session, owner_id)
    profile = await session.get(UserObjectStorage, owner_id)
    return {key: value[key] for key in defaults()} | {
        "credentials_configured": bool(value.get("credentials")),
        "version_id": value.get("version_id"),
        "execution_location": "cloud",
        "restart_required": False,
        "sync": profile.sync if profile else {},
    }


async def save_config(session, owner_id, body):
    # Serialize first saves and replacements on a row that already exists.
    user = await session.scalar(
        select(User).where(User.id == owner_id, User.is_active.is_(True)).with_for_update()
    )
    if user is None:
        raise missing("账号不可用，无法修改个人对象存储")
    profile = await session.get(UserObjectStorage, owner_id, populate_existing=True)
    old = await current_config(session, owner_id)
    if body.revision != old["revision"]:
        raise ConflictError("存储设置已被其他页面修改，请刷新后重试")
    if body.local_path is not None or body.allow_http:
        raise ConflictError("云端个人设置不允许修改服务器路径或启用 HTTP")
    value = storage.build_object_config(body, old)
    if value.get("endpoint"):
        from app.services.h3_media_staging_service import _public_https_url

        if not _public_https_url(value["endpoint"]):
            raise ConflictError("云端对象存储 Endpoint 必须为公网 HTTPS 地址")
    version_id = uuid4().hex
    value.update(
        revision=body.revision + 1, owner_id=owner_id, version_id=version_id, profile_id=version_id
    )
    # Explicit removal revokes retained versions too; switching buckets does not.
    if body.clear_credentials and not body.access_key:
        versions = (
            await session.scalars(
                select(ObjectStorageVersion).where(ObjectStorageVersion.owner_id == owner_id)
            )
        ).all()
        for version in versions:
            version.revoked = True
            version.config = {**version.config, "credentials": None}
    if profile is None:
        profile = UserObjectStorage(
            owner_id=owner_id, revision=value["revision"], version_id=version_id, cursor=0, sync={}
        )
        session.add(profile)
    else:
        changed = await session.scalar(
            update(UserObjectStorage)
            .where(
                UserObjectStorage.owner_id == owner_id, UserObjectStorage.revision == body.revision
            )
            .values(revision=value["revision"], version_id=version_id, cursor=0, sync={})
            .returning(UserObjectStorage.owner_id)
        )
        if changed is None:
            raise ConflictError("存储设置已被其他页面修改，请刷新后重试")
    session.add(
        ObjectStorageVersion(
            id=version_id, owner_id=owner_id, revision=value["revision"], config=value
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("存储设置已被其他页面修改，请刷新后重试") from None
    return await public_config(session, owner_id)


def reference(value):
    return {key: value[key] for key in ("owner_id", "version_id", "revision")}


async def resolve_reference(session, owner_id, ref):
    if (
        not isinstance(ref, dict)
        or ref.get("owner_id") != owner_id
        or not isinstance(ref.get("version_id"), str)
        or type(ref.get("revision")) is not int
    ):
        raise missing("任务缺少本人对象存储版本引用，未提交模型请求")
    version = await session.scalar(
        select(ObjectStorageVersion)
        .where(
            ObjectStorageVersion.id == ref["version_id"],
            ObjectStorageVersion.owner_id == owner_id,
            ObjectStorageVersion.revision == ref["revision"],
        )
        .execution_options(populate_existing=True)
    )
    if version is None or version.revoked or not version.config.get("credentials"):
        raise missing(
            "任务引用的对象存储密钥已清除或配置不可用；请重新配置并重新预检，未提交模型请求"
        )
    return dict(version.config)


async def h3_config(session, owner_id, *, ref=None, images=False, verify_connection=False):
    if settings.runtime_execution_location == "cloud":
        value = (
            await resolve_reference(session, owner_id, ref)
            if ref is not None
            else await current_config(session, owner_id)
        )
    else:
        value = storage.read_config()
    if not value.get("enabled") or not value.get("credentials"):
        raise missing("官方 MiniMax H3 需要先配置并启用本人的对象存储，请前往存储设置")
    if settings.runtime_execution_location == "cloud":
        try:
            keys = json.loads(decrypt_api_key(value["credentials"]))
            if (
                not isinstance(keys, list)
                or len(keys) != 2
                or not all(isinstance(key, str) and key for key in keys)
            ):
                raise ValueError()
        except Exception:
            raise missing("H3 对象存储密钥无法解密或不完整，请重新保存本人凭据") from None
    from app.services.h3_media_staging_service import _public_https_url

    if not _public_https_url(value.get("endpoint") or ""):
        raise missing("H3 对象存储 Endpoint 必须为公网 HTTPS 地址，请检查存储设置")
    if images and (
        type(value.get("signed_url_seconds")) is not int or value["signed_url_seconds"] < 3600
    ):
        raise missing("H3 参考素材签名有效期不足 1 小时，请在存储设置中调整")
    if verify_connection:
        try:
            await asyncio.to_thread(storage.check_connection, value)
        except Exception as exc:
            raise missing(
                storage.object_error_message(exc, "H3 对象存储只读预检") + "；未提交模型请求"
            ) from None
    return value


async def mirror_account(owner_id):
    """One bounded batch per account; compare version before updating progress."""
    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        user = await session.get(User, owner_id)
        if user is None or not user.is_active:
            return
        profile = await session.get(UserObjectStorage, owner_id)
        if profile is None:
            return
        value = await current_config(session, owner_id)
        if (
            not value.get("enabled")
            or not value.get("mirror_enabled")
            or not value.get("credentials")
        ):
            return
        version_id, cursor = profile.version_id, profile.cursor
        items = list(
            (
                await session.scalars(
                    select(MediaFile)
                    .where(MediaFile.owner_id == owner_id, MediaFile.id > cursor)
                    .order_by(MediaFile.id)
                    .limit(4)
                )
            ).all()
        )
        status = {"status": "ok", "checked_at": utcnow().isoformat()}
        for item in items:
            transfer = asyncio.create_task(
                asyncio.to_thread(storage.mirror_media, item, value=value)
            )
            try:
                await asyncio.shield(transfer)
            except asyncio.CancelledError:
                await asyncio.gather(transfer, return_exceptions=True)
                raise
            except Exception as exc:
                status.update(
                    status="error",
                    message=storage.object_error_message(exc, "个人云副本同步") + "；将重试该素材",
                )
                break
            cursor = item.id
        if not items:
            cursor = 0
        await session.execute(
            update(UserObjectStorage)
            .where(
                UserObjectStorage.owner_id == owner_id, UserObjectStorage.version_id == version_id
            )
            .values(cursor=cursor, sync=status)
        )
        await session.commit()


async def mirror_round():
    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        owners = list(
            (
                await session.scalars(
                    select(UserObjectStorage.owner_id).order_by(UserObjectStorage.owner_id)
                )
            ).all()
        )
    semaphore = asyncio.Semaphore(2)

    async def run(owner_id):
        async with semaphore:
            try:
                await mirror_account(owner_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A corrupt/revoked profile must not stop any other account.
                async with SessionLocal() as session:
                    await session.execute(
                        update(UserObjectStorage)
                        .where(UserObjectStorage.owner_id == owner_id)
                        .values(
                            sync={
                                "status": "error",
                                "message": "个人存储配置不可用，请检查并重新保存设置",
                            }
                        )
                    )
                    await session.commit()

    await asyncio.gather(*(run(owner_id) for owner_id in owners))
