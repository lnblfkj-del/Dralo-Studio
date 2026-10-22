"""Strict local decode and measured duration for generated MP3, before adoption."""

# ruff: noqa: RUF001

import math
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.storage_safety import finish_storage_io, write_storage_bytes
from app.providers.audio_transport import mp3_result
from app.services import canvas_processing_service as processing


async def inspect_audio(data, active=None):
    data = mp3_result(data)["audio_bytes"]
    ffmpeg, _ = processing.executables()
    root = settings.storage_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="audio-check-", dir=root) as directory:
        path = Path(directory) / "result.mp3"
        await finish_storage_io(write_storage_bytes, settings, path, data)
        info = await processing.probe(path, active)
        duration = float(info.get("duration") or 0)
        if not info.get("has_audio") or not math.isfinite(duration) or not 0 < duration <= 900:
            raise ConflictError("生成音频没有有效音轨或时长，结果已保留，未采用")
        await processing.command([
            ffmpeg, "-v", "error", "-xerror", "-err_detect", "explode",
            "-protocol_whitelist", "file", "-format_whitelist", "mp3",
            "-i", str(path), "-map", "0:a:0", "-f", "null", "-",
        ], timeout=60, active=active)
        return {"duration": duration}


async def reference_sample(data):
    """Bound reference samples without changing pitch or stretching short audio."""
    await inspect_audio(data)
    ffmpeg, _ = processing.executables()
    with tempfile.TemporaryDirectory(prefix="voice-sample-", dir=settings.storage_path.resolve()) as directory:
        source = Path(directory) / "source.mp3"
        output = Path(directory) / "sample.wav"
        await finish_storage_io(write_storage_bytes, settings, source, data)
        await processing.command([
            ffmpeg, "-v", "error", "-i", str(source), "-t", "5",
            "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", str(output),
        ], timeout=60, output=output)
        info = await processing.probe(output)
        duration = float(info.get("duration") or 0)
        if not info.get("has_audio") or not math.isfinite(duration) or not 0 < duration <= 5.001:
            raise ConflictError("参考声线截取失败，原始响应已保留")
        return {"audio_bytes": output.read_bytes(), "duration": duration}
