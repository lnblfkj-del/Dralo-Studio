"""Bounded image previews; originals remain unchanged."""
import asyncio
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from PIL import Image, ImageOps
from filelock import FileLock, Timeout

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.storage_safety import storage_reservation

_workers = asyncio.Semaphore(2)


async def get_thumbnail(media):
    if media.kind == "video":
        from app.services.media_processing_service import get_video_thumbnail
        return await get_video_thumbnail(media)
    if media.kind != "image":
        raise ConflictError("此媒体不支持图片预览")
    root = settings.storage_path.resolve()
    source = (root / media.file_path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise NotFoundError("媒体文件不存在")
    stat = source.stat()
    version = sha256(f"image-preview-v1:{media.hash}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    directory = (root / "cache" / "thumbnails" / str(media.owner_id) / "images-v1"
                 if settings.runtime_execution_location == "cloud"
                 else Path(gettempdir()) / "dralo-image-previews" / str(media.owner_id))
    target = directory / f"{media.id}-{version}.jpg"
    async with _workers:
        return await asyncio.to_thread(_locked_create, source, directory, target)


def _locked_create(source, directory, target):
    if not directory.exists():
        with storage_reservation(settings, 1024 * 1024, target=directory):
            directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ConflictError("预览缓存路径不可用")
    try:
        with FileLock(str(target) + ".lock", timeout=30):
            return _create(source, directory, target)
    except Timeout as exc:
        raise ConflictError("预览正在生成，请稍后重试") from exc


def _create(source, directory, target):
    if target.is_file() and not target.is_symlink():
        try:
            with Image.open(target) as cached:
                cached.load()
            return target
        except (OSError, ValueError):
            pass
    with storage_reservation(settings, 1024 * 1024, target=directory):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or target.is_symlink():
            raise ConflictError("预览缓存路径不可用")
        temporary = directory / f".{uuid4().hex}.jpg"
        try:
            with Image.open(source) as original:
                if original.width * original.height > 40_000_000:
                    raise ConflictError("图片过大，无法生成预览")
                image = ImageOps.exif_transpose(original)
                image.thumbnail((640, 360), Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    image = image.convert("RGB")
                image.save(temporary, "JPEG", quality=78, optimize=True)
            if temporary.stat().st_size > 1024 * 1024:
                raise ConflictError("图片预览大小异常")
            temporary.replace(target)
            return target
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ConflictError("图片预览生成失败") from exc
        finally:
            temporary.unlink(missing_ok=True)
