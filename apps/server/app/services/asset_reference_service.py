"""Reference-image validation and encoding for generation jobs."""

import base64
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import Job, MediaFile
from app.services.team_access import owner_scope

MAX_REFERENCE_IMAGES = 4
MAX_REFERENCE_BYTES = 20 * 1024 * 1024


def image_format(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise ConflictError("图片渠道返回了不支持的文件格式")


async def load_reference_images(
    session: AsyncSession, job: Job, media_ids: list[int], *, max_images: int = MAX_REFERENCE_IMAGES
) -> list[str]:
    if not media_ids:
        return []
    unique_ids = list(dict.fromkeys(media_ids))
    if type(max_images) is not int or max_images < 1 or len(unique_ids) > max_images:
        raise ConflictError(f"参考图片 {len(unique_ids)} 张，超过当前模型可加载上限 {max_images} 张")
    media_items = list(
        (
            await session.execute(
                select(MediaFile).where(
                    MediaFile.id.in_(unique_ids),
                    owner_scope(MediaFile.owner_id, job.owner_id),
                    MediaFile.kind == "image",
                )
            )
        ).scalars()
    )
    by_id = {item.id: item for item in media_items}
    storage_root = settings.storage_path.resolve()
    total_bytes = 0
    result: list[str] = []
    for media_id in unique_ids:
        media = by_id.get(media_id)
        if media is None:
            raise ConflictError("引用资产的最终图片不存在")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ConflictError("引用资产的最终图片不存在")
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > MAX_REFERENCE_BYTES:
            raise ConflictError("参考图片总大小不能超过 20 MB")
        mime_type = media.mime_type or image_format(data)[1]
        result.append(f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}")
    return result
