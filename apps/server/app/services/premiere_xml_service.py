"""Premiere-importable Final Cut Pro 7 XML packages for R11-B."""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXPORT,
    Job,
    MediaFile,
    ProjectMediaLink,
    SegmentVideoVersion,
)
from app.services import engineering_package_service
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.team_access import owner_scope, same_team

TARGET_TYPE = "episode_premiere_xml"
SCHEMA_VERSION = "episode_premiere_xml.v1"


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:120] or fallback


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(round(seconds * 1000), 0)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _path_url(relative: str) -> str:
    encoded = "/".join(quote(part) for part in Path(relative).as_posix().split("/"))
    return "file://localhost/./" + encoded


def _text(parent: ET.Element, tag: str, value: Any, **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, tag, attrs)
    child.text = str(value)
    return child


def _rate(parent: ET.Element, frame_rate: int) -> None:
    rate = ET.SubElement(parent, "rate")
    _text(rate, "timebase", frame_rate)
    _text(rate, "ntsc", "FALSE")


def _file_element(
    parent: ET.Element,
    *,
    file_id: str,
    name: str,
    path: str,
    duration: int,
    frame_rate: int,
    width: int | None = None,
    height: int | None = None,
    audio_only: bool = False,
) -> ET.Element:
    file_node = ET.SubElement(parent, "file", {"id": file_id})
    _text(file_node, "name", name)
    _text(file_node, "pathurl", _path_url(path))
    _rate(file_node, frame_rate)
    _text(file_node, "duration", max(duration, 1))
    media = ET.SubElement(file_node, "media")
    if not audio_only:
        video = ET.SubElement(media, "video")
        sample = ET.SubElement(video, "samplecharacteristics")
        _rate(sample, frame_rate)
        _text(sample, "width", width or 1280)
        _text(sample, "height", height or 720)
        _text(sample, "anamorphic", "FALSE")
        _text(sample, "pixelaspectratio", "square")
        _text(video, "duration", max(duration, 1))
    audio = ET.SubElement(media, "audio")
    sample = ET.SubElement(audio, "samplecharacteristics")
    _text(sample, "depth", 16)
    _text(sample, "samplerate", 48000)
    _text(audio, "channelcount", 2)
    return file_node


def build_xmeml(
    *, sequence_name: str, frame_rate: int, width: int, height: int,
    segments: list[dict[str, Any]], audio_clips: list[dict[str, Any]],
) -> bytes:
    """Build a conservative FCP7 xmeml sequence for Premiere importers."""
    xmeml = ET.Element("xmeml", {"version": "5"})
    sequence = ET.SubElement(xmeml, "sequence", {"id": "sequence-1"})
    _text(sequence, "name", sequence_name)
    _text(sequence, "duration", max((int(item["end_frame"]) for item in segments), default=0))
    _rate(sequence, frame_rate)
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sample = ET.SubElement(fmt, "samplecharacteristics")
    _rate(sample, frame_rate)
    _text(sample, "width", width)
    _text(sample, "height", height)
    _text(sample, "anamorphic", "FALSE")
    _text(sample, "pixelaspectratio", "square")
    video_track = ET.SubElement(video, "track")
    audio_parent = ET.SubElement(media, "audio")
    native_audio_track = ET.SubElement(audio_parent, "track")
    for index, item in enumerate(segments, start=1):
        video_id = f"video-clip-{index}"
        audio_id = f"native-audio-{index}"
        file_id = f"segment-file-{index}"
        for track, clip_id, audio_only in (
            (video_track, video_id, False), (native_audio_track, audio_id, True)
        ):
            clip = ET.SubElement(track, "clipitem", {"id": clip_id})
            _text(clip, "name", item["name"])
            _text(clip, "duration", item["source_duration_frames"])
            _rate(clip, frame_rate)
            _text(clip, "start", item["start_frame"])
            _text(clip, "end", item["end_frame"])
            _text(clip, "in", item["in_frame"])
            _text(clip, "out", item["out_frame"])
            if audio_only:
                ET.SubElement(clip, "file", {"id": file_id})
            else:
                _file_element(
                    clip, file_id=file_id, name=item["name"], path=item["path"],
                    duration=item["source_duration_frames"], frame_rate=frame_rate,
                    width=width, height=height,
                )
            source_track = ET.SubElement(clip, "sourcetrack")
            _text(source_track, "mediatype", "audio" if audio_only else "video")
            _text(source_track, "trackindex", 1)
            for linked_id, media_type in ((video_id, "video"), (audio_id, "audio")):
                link = ET.SubElement(clip, "link")
                _text(link, "linkclipref", linked_id)
                _text(link, "mediatype", media_type)
                _text(link, "trackindex", 1)
                _text(link, "clipindex", index)
    for track_index, item in enumerate(audio_clips, start=2):
        track = ET.SubElement(audio_parent, "track")
        clip = ET.SubElement(track, "clipitem", {"id": f"external-audio-{track_index}"})
        _text(clip, "name", item["name"])
        _text(clip, "duration", item["source_duration_frames"])
        _rate(clip, frame_rate)
        _text(clip, "start", item["start_frame"])
        _text(clip, "end", item["end_frame"])
        _text(clip, "in", item.get("in_frame", 0))
        _text(clip, "out", item["out_frame"])
        _file_element(
            clip, file_id=f"audio-file-{track_index}", name=item["name"],
            path=item["path"], duration=item["source_duration_frames"],
            frame_rate=frame_rate, audio_only=True,
        )
        source_track = ET.SubElement(clip, "sourcetrack")
        _text(source_track, "mediatype", "audio")
        _text(source_track, "trackindex", 1)
        labels = ET.SubElement(clip, "labels")
        _text(labels, "label2", item["role"])
    xml = ET.tostring(xmeml, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + xml


async def _audio_sources(
    session: AsyncSession, *, owner_id: int, payload: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested: list[dict[str, Any]] = []
    background_id = payload.get("background_audio_media_id")
    episode_duration = sum(float(item.get("timeline_duration") or 0) for item in snapshot.get("segments") or [])
    if background_id:
        requested.append({
            "media_id": int(background_id), "role": "background_music", "name": "整集配乐",
            "start_time": 0.0, "end_time": episode_duration,
            "gain": float(payload.get("background_audio_volume", 0.3)), "loop": True,
        })
    for index, cue in enumerate(snapshot.get("dialogue_cues") or [], start=1):
        if cue.get("audio_media_id"):
            requested.append({
                "media_id": int(cue["audio_media_id"]), "role": "dialogue",
                "name": f"对白-{index:03d}", "start_time": float(cue.get("start_time") or 0),
                "end_time": float(cue.get("end_time") or 0), "gain": float(cue.get("gain", 1)),
                "loop": False, "audio_mode": cue.get("audio_mode", "replace"),
            })
    for index, cue in enumerate(snapshot.get("sound_cues") or [], start=1):
        requested.append({
            "media_id": int(cue["audio_media_id"]), "role": str(cue["kind"]),
            "name": str(cue.get("label") or f"声音-{index:03d}"),
            "start_time": float(cue.get("start_time") or 0), "end_time": float(cue.get("end_time") or 0),
            "gain": float(cue.get("gain", 1)), "loop": bool(cue.get("loop", False)),
        })
    issues: list[dict[str, Any]] = []
    resolved = []
    root = settings.storage_path.resolve()
    for item in requested:
        media = await session.get(MediaFile, item["media_id"])
        path = (root / Path(media.file_path)).resolve() if media else None
        if media is None or media.kind != "audio" or not await same_team(
            session, media.owner_id, owner_id
        ):
            issues.append({"code": "AUDIO_MEDIA_INVALID", "message": f"{item['name']}音频不存在、类型错误或无权使用"})
            continue
        if not path.is_relative_to(root) or not path.is_file():
            issues.append({"code": "AUDIO_FILE_MISSING", "message": f"{item['name']}音频文件不存在"})
            continue
        resolved.append({**item, "media": media, "source": path})
    return resolved, issues


async def preflight(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int
) -> dict[str, Any]:
    base = await engineering_package_service.preflight(
        session, owner_id, project_id=project_id, episode_id=episode_id
    )
    export_job = base.get("_export_job")
    payload = dict(export_job.payload or {}) if export_job else {}
    audio, audio_issues = await _audio_sources(
        session, owner_id=owner_id, payload=payload, snapshot=base.get("_snapshot") or {}
    )
    issues = [*base["issues"], *audio_issues]
    fingerprint_data = {
        "schema_version": SCHEMA_VERSION,
        "source_package_fingerprint": base["package_fingerprint"],
        "audio": [{
            "media_id": item["media_id"], "hash": item["media"].hash,
            "role": item["role"], "start_time": item["start_time"], "end_time": item["end_time"],
            "gain": item["gain"], "loop": item["loop"],
        } for item in audio],
    }
    fingerprint = sha256(json.dumps(
        fingerprint_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    return {
        **{key: value for key, value in base.items() if not key.startswith("_")},
        "status": "blocked" if issues else "ready",
        "package_fingerprint": fingerprint,
        "source_package_fingerprint": base["package_fingerprint"],
        "audio_count": len(audio),
        "files": ["premiere.xml", "media/segments/*.mp4", "media/audio/*", "subtitles/episode.srt", "compatibility.json", "manifest.json", "checksums.sha256"],
        "issues": issues, "_base": base, "_audio": audio,
    }


async def create_job(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int,
    request_id: str, expected_package_fingerprint: str,
) -> Job:
    existing = list((await session.scalars(select(Job).where(
        owner_scope(Job.owner_id, owner_id), Job.project_id == project_id,
        Job.target_type == TARGET_TYPE, Job.target_id == episode_id,
    ).order_by(Job.id.desc()))).all())
    for item in existing:
        if (item.payload or {}).get("request_id") == request_id:
            return item
    if any(item.status not in TERMINAL_STATUSES for item in existing):
        raise ConflictError("本集已有进行中的 Premiere XML 导出任务")
    result = await preflight(session, owner_id, project_id=project_id, episode_id=episode_id)
    if result["package_fingerprint"] != expected_package_fingerprint:
        raise ConflictError("Premiere XML 来源已变化，请重新预检")
    if result["status"] != "ready":
        raise ConflictError(result["issues"][0]["message"] if result["issues"] else "Premiere XML 预检未通过")
    source_job = result["_base"]["_export_job"]
    source_payload = dict(source_job.payload or {})
    job = Job(
        owner_id=owner_id, project_id=project_id, job_type=JOB_TYPE_EXPORT,
        status=JOB_STATUS_QUEUED, target_type=TARGET_TYPE, target_id=episode_id,
        payload={
            "request_id": request_id, "episode_id": episode_id,
            "package_fingerprint": expected_package_fingerprint,
            "source_package_fingerprint": result["source_package_fingerprint"],
            "export_job_id": source_job.id,
            "final_media_file_id": result["final_media_file_id"],
            "export_snapshot_fingerprint": result["export_snapshot_fingerprint"],
            "segment_video_version_ids": list(source_payload.get("segment_video_version_ids") or []),
            "production_snapshot": result["_base"]["_snapshot"],
            "background_audio_media_id": source_payload.get("background_audio_media_id"),
            "background_audio_volume": source_payload.get("background_audio_volume", 0.3),
            "pricing_snapshot": {"status": "estimated", "currency": "CNY", "amount": "0", "estimated_cents": 0, "reason": "本地生成 XML 与素材包，不调用外部渠道", "pricing_version": "local-premiere-xml-v1"},
        }, cost_estimate=0, max_attempts=1,
    )
    session.add(job)
    await session.flush()
    return job


async def build_package(session: AsyncSession, job: Job) -> MediaFile:
    payload = dict(job.payload or {})
    result = await preflight(session, job.owner_id, project_id=int(job.project_id), episode_id=int(payload["episode_id"]))
    if result["status"] != "ready" or result["package_fingerprint"] != payload.get("package_fingerprint"):
        raise ConflictError("Premiere XML 来源已变化或文件缺失，请重新预检")
    base = result["_base"]
    episode = base["_episode"]
    snapshot = base["_snapshot"]
    output_spec = snapshot.get("output_spec") or {}
    frame_rate = int(output_spec.get("frame_rate") or 24)
    width = int(output_spec.get("width") or 1280)
    height = int(output_spec.get("height") or 720)
    storage_root = settings.storage_path.resolve()
    relative = Path("projects") / str(job.project_id) / "premiere_xml" / f"{uuid4().hex}.zip"
    output = settings.storage_path / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    title = _safe_name(episode.title or f"第{episode.number}集", f"episode-{episode.number}")
    root = f"第{episode.number}集-{title}-Premiere-XML"
    version_ids = [int(value) for value in payload.get("segment_video_version_ids") or []]
    versions = list((await session.scalars(select(SegmentVideoVersion).where(SegmentVideoVersion.id.in_(version_ids)))).all())
    version_by_id = {item.id: item for item in versions}
    media_entries: list[tuple[str, Path, dict[str, Any]]] = []
    xml_segments: list[dict[str, Any]] = []
    manifest_segments: list[dict[str, Any]] = []
    cursor_seconds = 0.0
    for fallback_order, (segment, version_id) in enumerate(zip(snapshot.get("segments") or [], version_ids, strict=True), start=1):
        version = version_by_id[version_id]
        media = await session.get(MediaFile, version.media_file_id)
        source = (storage_root / Path(media.file_path)).resolve()
        order = int(segment.get("order") or fallback_order)
        archive_path = f"media/segments/{order:03d}-{_safe_name(str(segment.get('title') or '片段'), 'segment')}.mp4"
        media_entries.append((archive_path, source, {"media_file_id": media.id, "role": "segment"}))
        start_frame = round(cursor_seconds * frame_rate)
        cursor_seconds += float(segment.get("timeline_duration") or 0)
        end_frame = round(cursor_seconds * frame_rate)
        in_frame = round(float(segment.get("trim_in") or 0) * frame_rate)
        source_duration = float(segment.get("generation_duration") or media.duration or (end_frame - start_frame) / frame_rate)
        xml_segments.append({
            "name": str(segment.get("title") or f"片段 {order}"), "path": archive_path,
            "start_frame": start_frame, "end_frame": end_frame, "in_frame": in_frame,
            "out_frame": in_frame + end_frame - start_frame,
            "source_duration_frames": max(round(source_duration * frame_rate), in_frame + end_frame - start_frame),
        })
        manifest_segments.append({
            "segment_id": segment.get("segment_id"), "order": order, "title": segment.get("title"),
            "video_version_id": version.id, "media_file_id": media.id, "path": archive_path,
            "timeline_start_frame": start_frame, "timeline_end_frame": end_frame,
            "source_in_frame": in_frame, "source_out_frame": in_frame + end_frame - start_frame,
        })
    audio_path_by_media: dict[int, str] = {}
    audio_rows = []
    for index, item in enumerate(result["_audio"], start=1):
        media: MediaFile = item["media"]
        if media.id not in audio_path_by_media:
            suffix = Path(media.original_name or media.file_path).suffix or Path(media.file_path).suffix or ".audio"
            stem = _safe_name(Path(media.original_name or f"audio-{media.id}").stem, f"audio-{media.id}")
            audio_path_by_media[media.id] = f"media/audio/{index:03d}-{stem}{suffix.lower()}"
            media_entries.append((audio_path_by_media[media.id], item["source"], {"media_file_id": media.id, "role": "audio"}))
        duration = max(float(item["end_time"]) - float(item["start_time"]), 0.001)
        audio_rows.append({
            "name": item["name"], "role": item["role"], "path": audio_path_by_media[media.id],
            "start_frame": round(float(item["start_time"]) * frame_rate),
            "end_frame": round(float(item["end_time"]) * frame_rate),
            "source_duration_frames": max(round(float(media.duration or duration) * frame_rate), 1),
            "out_frame": round(duration * frame_rate), "gain": item["gain"], "loop": item["loop"],
            "audio_mode": item.get("audio_mode"), "media_file_id": media.id,
        })
    xml_data = build_xmeml(
        sequence_name=f"第{episode.number}集 {episode.title or ''}", frame_rate=frame_rate,
        width=width, height=height, segments=xml_segments, audio_clips=audio_rows,
    )
    subtitles = []
    for index, item in enumerate(snapshot.get("subtitle_source") or [], start=1):
        text = str(item.get("text") or "").strip()
        if text:
            subtitles.extend([str(index), f"{_srt_timestamp(float(item['start_time']))} --> {_srt_timestamp(float(item['end_time']))}", text, ""])
    compatibility = {
        "format": "Final Cut Pro 7 XML (xmeml v5)",
        "display_name": "Premiere 可导入工程（XML）", "native_prproj": False,
        "structural_validation": True, "premiere_application_validation": False,
        "relink_strategy": "XML 使用包内相对 file://localhost/./ 路径；若 Premiere 未自动定位，请将离线素材批量重链到 media 目录。",
        "supported": ["segment_order", "source_trim", "sequence_dimensions", "frame_rate", "native_segment_audio", "external_audio_sources", "srt_sidecar"],
        "limitations": [
            "字幕以独立 SRT 提供，需要在 Premiere 中单独导入。",
            "FCP7 XML 不承诺复现平台专属转场、滤镜、调色和字幕样式。",
            "后期对白 replace 模式的原声区间静音无法在基础 FCP7 XML 中无损表达，导入后需人工核对。",
            "循环配乐或环境声仅保存来源与目标区间，导入后需核对循环铺设。",
            "音量 gain 保存于 manifest，基础 XML 不保证所有 Premiere 版本一致解释。",
        ],
    }
    text_files: dict[str, bytes] = {
        "premiere.xml": xml_data,
        "subtitles/episode.srt": ("\n".join(subtitles) + ("\n" if subtitles else "")).encode("utf-8-sig"),
        "compatibility.json": json.dumps(compatibility, ensure_ascii=False, indent=2).encode(),
    }
    records = [{"path": path, "size": len(data), "sha256": sha256(data).hexdigest(), "role": "metadata"} for path, data in text_files.items()]
    records.extend({"path": path, "size": source.stat().st_size, "sha256": _digest_file(source), **metadata} for path, source, metadata in media_entries)
    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_at": datetime.now(UTC).isoformat(),
        "package_fingerprint": payload["package_fingerprint"], "source_package_fingerprint": payload["source_package_fingerprint"],
        "export_snapshot_fingerprint": payload.get("export_snapshot_fingerprint"), "project_id": job.project_id,
        "episode": {"id": episode.id, "number": episode.number, "title": episode.title, "script_revision": episode.script_revision},
        "sequence": {"frame_rate": frame_rate, "width": width, "height": height, "duration_frames": max((item["end_frame"] for item in xml_segments), default=0)},
        "segments": manifest_segments, "audio": audio_rows, "files": records,
        "validation": {"xml_parser": "passed", "premiere_application": "not_run"},
    }
    manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    text_files["manifest.json"] = manifest_data
    checksums = [f"{item['sha256']}  {item['path']}" for item in sorted(records, key=lambda row: row["path"])]
    checksums.append(f"{sha256(manifest_data).hexdigest()}  manifest.json")
    text_files["checksums.sha256"] = ("\n".join(checksums) + "\n").encode("ascii")
    try:
        with ZipFile(output, "w", allowZip64=True) as archive:
            for path, data in text_files.items():
                archive.writestr(f"{root}/{path}", data, compress_type=ZIP_DEFLATED)
            for path, source, _metadata in media_entries:
                archive.write(source, f"{root}/{path}", compress_type=ZIP_STORED)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    media = MediaFile(
        project_id=job.project_id, owner_id=job.owner_id, kind="file", source="export",
        file_path=relative.as_posix(), original_name=f"第{episode.number}集-{title}-Premiere可导入工程-XML.zip",
        mime_type="application/zip", size=output.stat().st_size, hash=_digest_file(output),
    )
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=int(job.project_id), media_file_id=media.id))
    await session.flush()
    return media


async def list_versions(session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int) -> list[dict[str, Any]]:
    await engineering_package_service._episode(session, owner_id, project_id, episode_id)
    jobs = list((await session.scalars(select(Job).where(
        owner_scope(Job.owner_id, owner_id), Job.project_id == project_id,
        Job.target_type == TARGET_TYPE, Job.target_id == episode_id, Job.status == JOB_STATUS_SUCCEEDED,
    ).order_by(Job.id))).all())
    versions = []
    root = settings.storage_path.resolve()
    for version, job in enumerate(jobs, start=1):
        media_id = int((job.result or {}).get("media_file_id") or 0)
        media = await session.get(MediaFile, media_id) if media_id else None
        path = (root / Path(media.file_path)).resolve() if media else None
        versions.append({
            "version": version, "job_id": job.id, "media_file_id": media_id,
            "original_name": media.original_name if media else None, "size": media.size if media else None,
            "hash": media.hash if media else None, "created_at": job.finished_at or job.created_at,
            "available": bool(path and path.is_relative_to(root) and path.is_file()),
            "package_fingerprint": (job.payload or {}).get("package_fingerprint"),
            "application_validation": "not_run",
        })
    return list(reversed(versions))
