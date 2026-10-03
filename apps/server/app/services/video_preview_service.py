"""Bounded background proxy generation; originals always remain authoritative."""
import asyncio
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import time
from tempfile import gettempdir
from uuid import uuid4

from filelock import FileLock

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.storage_safety import finish_storage_io, storage_reservation

CHUNK_SIZE = 1024 * 1024
MAX_PROXY = 96 * CHUNK_SIZE
_tasks = {}
_workers = asyncio.Semaphore(1)
_failed = {}


def location(media):
    root = settings.storage_path.resolve()
    source = (root / media.file_path).resolve()
    if media.kind != "video" or not source.is_relative_to(root) or not source.is_file():
        raise NotFoundError("视频素材不存在")
    stat = source.stat()
    version = sha256(f"proxy-v1:{media.hash}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}".encode()).hexdigest()
    directory = (root / "cache" / "thumbnails" / str(media.owner_id) / "video-proxies-v1"
                 if settings.runtime_execution_location == "cloud"
                 else Path(gettempdir()) / "dralo-video-proxies" / str(media.owner_id))
    return source, directory / f"{media.id}-{version}.mp4", version


def ready(media):
    _, target, version = location(media)
    if target.parent.is_symlink() or target.is_symlink():
        return None
    try:
        stamp = target.stat()
        sidecar = target.with_suffix(".json")
        if sidecar.is_symlink() or sidecar.stat().st_size > 4096:
            return None
        meta = json.loads(sidecar.read_text("utf-8"))
        if (meta["version"] != version or meta["size"] != stamp.st_size or meta["mtime"] != stamp.st_mtime_ns
                or not 0 < stamp.st_size < MAX_PROXY):
            return None
        return target, meta
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def request_preview(media):
    cached = ready(media)
    if cached:
        return cached
    source, target, version = location(media)
    if source.stat().st_size > 1024 ** 3 or len(_tasks) >= 4:
        return None
    key = str(target)
    if _failed.get(key, 0) > time.monotonic():
        return None
    if key not in _tasks:
        async def generate():
            async with _workers:
                await finish_storage_io(_generate, source, target, version)
        task = asyncio.create_task(generate())
        _tasks[key] = task
        def finished(done):
            _tasks.pop(key, None)
            if not done.cancelled():
                if done.exception():
                    if len(_failed) >= 128:
                        _failed.pop(next(iter(_failed)))
                    _failed[key] = time.monotonic() + 60
        task.add_done_callback(finished)
    return None


def _duration(path):
    result = subprocess.run([settings.ffprobe_path, "-v", "error", "-protocol_whitelist", "file",
                             "-format_whitelist", "mov,matroska,webm,avi", "-show_entries", "format=duration",
                             "-of", "json", str(path)], capture_output=True, timeout=15, check=True)
    value = float(json.loads(result.stdout)["format"]["duration"])
    if not math.isfinite(value) or not 0 < value <= 600:
        raise ValueError("Unsupported proxy duration")
    return value


def _generate(source, target, version):
    directory = target.parent
    with storage_reservation(settings, MAX_PROXY, target=directory):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or target.is_symlink():
            raise ValueError("Unsafe proxy directory")
        with FileLock(str(target) + ".lock", timeout=1):
            original_stat = source.stat()
            duration = _duration(source)
            temporary = directory / f".{uuid4().hex}.mp4"
            metadata = directory / f".{uuid4().hex}.json"
            try:
                result = subprocess.run([
                    settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-threads", "1", "-filter_threads", "1", "-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm,avi",
                    "-max_alloc", "268435456", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                    "-vf", "scale=w='min(854,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-maxrate", "1000k", "-bufsize", "2000k",
                    "-threads", "1", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", "-fs", str(MAX_PROXY), "-y", str(temporary),
                ], capture_output=True, timeout=120, check=False)
                stat = temporary.stat()
                if (result.returncode or not 0 < stat.st_size < MAX_PROXY or abs(_duration(temporary) - duration) > .25
                        or any(getattr(source.stat(), field) != getattr(original_stat, field)
                               for field in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino"))):
                    raise ValueError("Incomplete or outdated proxy")
                checksum = sha256()
                with temporary.open("rb") as stream:
                    for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
                        checksum.update(block)
                meta = {"version": version, "size": stat.st_size, "mtime": stat.st_mtime_ns, "duration": duration,
                        "hash": checksum.hexdigest()}
                metadata.write_text(json.dumps(meta), "utf-8")
                temporary.replace(target)
                metadata.replace(target.with_suffix(".json"))
            finally:
                temporary.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)


def require_preview(media):
    value = ready(media)
    if not value:
        raise NotFoundError("轻量预览尚未就绪，请使用原片播放")
    return value


def chunk(media, index):
    target, meta = require_preview(media)
    if index < 0 or index * CHUNK_SIZE >= meta["size"]:
        raise NotFoundError("预览分段不存在")
    with target.open("rb") as stream:
        stream.seek(index * CHUNK_SIZE)
        raw = stream.read(CHUNK_SIZE)
    return raw, {"id": media.id, "size": len(raw), "hash": sha256(raw).hexdigest(),
                 "mime_type": "video/mp4", "preview_chunk": True, "preview_version": meta["version"],
                 "updated_at": f"{meta['version']}:{meta['hash']}:{index}"}
