"""Explicit complete-speech evidence; music and voice samples are not timing."""

# ruff: noqa: RUF001
import hashlib
import json
import math

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Job
from app.schemas.episode_timing import ContentAnalysis
from app.services.episode_content_timing import validate_analysis


def speech_fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def resolve_speech_timing(session, media, source):
    if source.kind not in {"dialogue", "narration"} or not source.spoken_text:
        raise ConflictError("实测估时只能绑定一条完整对白或旁白，不能使用音乐或动作说明")
    duration = media.duration
    if type(duration) not in {int, float} or not math.isfinite(duration) or not 0 < duration <= 900:
        raise ConflictError("配音缺少有效实测时长，请先完成音频生成和入库")
    job = await session.scalar(select(Job).where(
        Job.job_type == "tts", Job.status == "succeeded", Job.owner_id == media.owner_id,
        Job.result["media_file_id"].as_integer() == media.id,
    ).order_by(Job.id.desc()).limit(1))
    measured = (job.result or {}).get("duration") if job else None
    if (job is None or job.payload.get("prompt") != source.spoken_text
            or type(measured) not in {int, float} or not math.isfinite(measured)
            or abs(measured - duration) > 0.001):
        raise ConflictError("所选音频不是这条正文的完整实测配音；声线参考不能用于对白估时")
    return {"source_key": source.key, "duration_ms": math.ceil(duration * 1000),
            "media_id": media.id, "media_hash": media.hash, "job_id": job.id,
            "spoken_fingerprint": speech_fingerprint(source.spoken_text)}


def calibrate_analysis(sources, analysis, references):
    timings = [item["speech_timing"] for item in references if item.get("speech_timing")]
    if not timings:
        return analysis
    validate_analysis(sources, analysis)
    by_key = {item["source_key"]: item for item in timings}
    if len(by_key) != len(timings):
        raise ValueError("同一条对白不能绑定多个实测配音")
    units = {unit.key: unit for unit in sources.units}
    for key, timing in by_key.items():
        source = units.get(key)
        if (source is None or source.kind not in {"dialogue", "narration"}
                or timing.get("spoken_fingerprint") != speech_fingerprint(source.spoken_text)
                or type(timing.get("duration_ms")) is not int
                or not 0 < timing["duration_ms"] <= 900_000):
            raise ValueError("实测配音与当前正文不一致，请重新核对绑定")
    raw = analysis.model_dump(mode="json")
    for block in raw["blocks"]:
        delta = 0
        for event in block["events"]:
            original_maximum = event["maximum_ms"]
            if block["overlap_basis"] is None:
                event["start_ms"] += delta
            timing = by_key.get(event["source_key"])
            if timing:
                for field in ("minimum_ms", "estimated_ms", "maximum_ms"):
                    event[field] = timing["duration_ms"]
                event["basis"] = f"完整配音实测：媒体 #{timing['media_id']}，{timing['duration_ms']} 毫秒；不加速、不截断。"
            if block["overlap_basis"] is None:
                delta += event["maximum_ms"] - original_maximum
    calibrated = ContentAnalysis.model_validate_json(json.dumps(raw))
    # Preserve parallel starts; never invent a new overlap to fit longer speech.
    validate_analysis(sources, calibrated)
    return calibrated
