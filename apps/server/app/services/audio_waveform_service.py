"""Small, versioned waveform derivatives; never transfer originals for visualization."""
import asyncio
from array import array
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import gettempdir
from uuid import uuid4

from filelock import FileLock, Timeout

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.storage_safety import finish_storage_io, storage_reservation

_workers = asyncio.Semaphore(1)
MAX_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 180
SAMPLES = 8000 * MAX_SECONDS


async def get_waveform(media):
    if media.kind != "audio":
        raise ConflictError("此媒体不支持音频波形")
    root = settings.storage_path.resolve()
    source = (root / media.file_path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise NotFoundError("媒体文件不存在")
    stat = source.stat()
    if stat.st_size > MAX_BYTES:
        raise ConflictError("波形预览上限为 25 MB / 180 秒")
    if media.duration is not None and media.duration > MAX_SECONDS:
        raise ConflictError("波形预览上限为 25 MB / 180 秒")
    version = sha256(f"waveform-v1:{media.hash}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}".encode()).hexdigest()
    directory = (root / "cache" / "thumbnails" / str(media.owner_id) / "audio-waveforms-v1"
                 if settings.runtime_execution_location == "cloud"
                 else Path(gettempdir()) / "dralo-waveforms" / str(media.owner_id))
    target = directory / f"{media.id}-{version}.json"
    async with _workers:
        return await finish_storage_io(_locked_create, source, directory, target)


def _valid(value):
    import math
    return (isinstance(value, dict) and type(value.get("duration")) in (int, float)
            and 0 < value["duration"] <= MAX_SECONDS and isinstance(value.get("peaks"), list)
            and len(value["peaks"]) == 512
            and all(type(peak) in (int, float) and math.isfinite(peak) and 0 <= peak <= 1 for peak in value["peaks"]))


def _locked_create(source, directory, target):
    with storage_reservation(settings, 128 * 1024, target=directory):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or target.is_symlink():
            raise ConflictError("波形缓存路径不可用")
        try:
            with FileLock(str(target) + ".lock", timeout=35):
                if target.is_file() and target.stat().st_size <= 128 * 1024:
                    try:
                        if _valid(json.loads(target.read_text("utf-8"))):
                            return target
                    except (ValueError, OSError):
                        pass
                temporary = directory / f".{uuid4().hex}.json"
                try:
                    result = subprocess.run([
                        settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-threads", "1", "-protocol_whitelist", "file,pipe", "-max_alloc", "268435456",
                        "-format_whitelist", "wav,mp3,mov,ogg",
                        "-i", str(source), "-t", str(MAX_SECONDS + 1), "-vn", "-ac", "1", "-ar", "8000",
                        "-threads", "1", "-f", "s16le", "pipe:1",
                    ], capture_output=True, timeout=30, check=False)
                    if result.returncode or not result.stdout or len(result.stdout) > SAMPLES * 2:
                        raise ConflictError("波形生成失败或音频超过 180 秒")
                    samples = array("h")
                    samples.frombytes(result.stdout)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    peaks = [round(max((abs(sample) / 32768 for sample in samples[
                        index * len(samples) // 512:(index + 1) * len(samples) // 512
                    ]), default=0), 5) for index in range(512)]
                    temporary.write_text(json.dumps({"duration": len(samples) / 8000, "peaks": peaks}, separators=(",", ":")), "utf-8")
                    temporary.replace(target)
                    return target
                except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                    raise ConflictError("波形生成失败，请使用音频播放器") from exc
                finally:
                    temporary.unlink(missing_ok=True)
        except Timeout as exc:
            raise ConflictError("波形正在生成，请稍后重试") from exc
