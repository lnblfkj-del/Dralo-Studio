"""Administrator-only storage control; owners can explicitly publish their own media."""
import asyncio
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.errors import ConflictError
from app.core.config import settings
from app.services import storage_service as storage
from app.services.media_service import get_owned_media

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("/directories")
async def storage_directories(request: Request, _user: AdminUser, path: str = ""):
    # Do not expose host directory names to remote/cloud clients.
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise ConflictError("目录选择仅允许本机访问")
    return await asyncio.to_thread(storage.list_local_directories, path)


@router.get("")
async def get_storage(_user: AdminUser, session: SessionDep):
    if settings.runtime_execution_location == "cloud":
        from app.services import private_storage_service
        return await private_storage_service.public_config(session, _user.id)
    return storage.public_config()


@router.put("")
async def update_storage(body: storage.StorageUpdate, _user: AdminUser, session: SessionDep):
    if settings.runtime_execution_location == "cloud":
        from app.services import private_storage_service
        result = await private_storage_service.save_config(session, _user.id, body)
        await session.commit()
        return result
    return await asyncio.to_thread(storage.save_config, body)


@router.post("/test")
async def test_storage(_user: AdminUser, session: SessionDep):
    value = None
    if settings.runtime_execution_location == "cloud":
        from app.services import private_storage_service
        value = await private_storage_service.current_config(session, _user.id)
        if not value.get("enabled") or not value.get("credentials"):
            raise ConflictError("请先保存并启用本人的对象存储配置")
    try:
        await asyncio.to_thread(storage.check_connection, value)
    except Exception as exc:
        raise ConflictError(storage.object_error_message(exc, "存储连接检查") + "；未上传或删除任何对象") from None
    return {"ok":True,"message":"桶连接与读取元信息通过；未执行收费素材上传，不代表已验证写入权限"}


class PublishRequest(BaseModel):
    confirm_upload: bool = False


class CreateDirectoryRequest(BaseModel):
    parent: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=120)


@router.post("/directories")
async def create_storage_directory(body: CreateDirectoryRequest, _user: AdminUser):
    await asyncio.to_thread(storage.create_local_directory, body.parent, body.name)
    return await asyncio.to_thread(storage.list_local_directories, body.parent)


@router.post("/media/{media_id}/signed-url")
async def publish_media(media_id: int, body: PublishRequest, session: SessionDep, user: CurrentUser):
    if not body.confirm_upload:
        raise ConflictError("需要明确确认上传素材并创建短时访问链接")
    media = await get_owned_media(session, media_id, user.id)
    if settings.runtime_execution_location == "cloud":
        from app.services import private_storage_service
        if media.owner_id != user.id:
            raise ConflictError("只能将本人素材发布到本人的对象存储")
        value = await private_storage_service.current_config(session, user.id)
        return await asyncio.to_thread(storage.mirror_media, media, make_url=True, value=value)
    return await asyncio.to_thread(storage.mirror_media, media, make_url=True)
