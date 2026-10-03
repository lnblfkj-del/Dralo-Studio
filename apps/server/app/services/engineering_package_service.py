"""Portable episode engineering packages for R11-A."""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.models import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXPORT,
    Episode,
    EpisodeProduction,
    Job,
    MediaFile,
    Project,
    ProjectMediaLink,
    SegmentVideoVersion,
)
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.team_access import owner_scope

TARGET_TYPE = "episode_engineering_package"
SCHEMA_VERSION = "episode_engineering_package.v1"


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\|?*\x00-]', "_", value).strip(" .")
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


async def _episode(session: AsyncSession, owner_id: int, project_id: int, episode_id: int) -> Episode:
    episode = await session.scalar(select(Episode).where(
        Episode.id == episode_id,
        Episode.project_id == project_id,
        Episode.status != "archived",
        owner_scope(Episode.owner_id, owner_id),
    ))
    if episode is None:
        raise NotFoundError("分集不存在")
    return episode


async def preflight(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
) -> dict[str, Any]:
    episode = await _episode(session, owner_id, project_id, episode_id)
    project = await session.get(Project, project_id)
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    issues: list[dict[str, Any]] = []
    from app.services.episode_edit_export_guard import EDIT_DRAFT_EXPORT_MESSAGE, pending_edit_draft

    pending_edit = await pending_edit_draft(session, episode.id)
    if pending_edit is not None:
        issues.append({"code": "EDIT_DRAFT_NOT_RENDERED", "message": EDIT_DRAFT_EXPORT_MESSAGE})
    final_media = (
        await session.get(MediaFile, production.final_media_file_id)
        if production and production.final_media_file_id
        else None
    )
    if final_media is None:
        issues.append({"code": "FINAL_MEDIA_MISSING", "message": "本集尚未生成当前整集成片"})
    export_jobs = list((await session.scalars(
        select(Job).where(
            owner_scope(Job.owner_id, owner_id),
            Job.project_id == project_id,
            Job.target_type == "episode_export",
            Job.target_id == episode.id,
            Job.status == JOB_STATUS_SUCCEEDED,
        ).order_by(Job.id.desc())
    )).all())
    export_job = next((item for item in export_jobs if int((item.result or {}).get("media_file_id") or 0) == getattr(final_media, "id", 0)), None)
    if final_media is not None and export_job is None:
        issues.append({"code": "EXPORT_EVIDENCE_MISSING", "message": "当前成片缺少成功合成快照，不能生成可追溯工程包"})
    payload = dict(export_job.payload or {}) if export_job else {}
    snapshot = dict(payload.get("production_snapshot") or {})
    if export_job and not snapshot:
        issues.append({"code": "SNAPSHOT_MISSING", "message": "当前成片没有冻结制作快照"})
    if snapshot and int(snapshot.get("source_script_revision") or -1) != episode.script_revision:
        issues.append({"code": "SCRIPT_STALE", "message": "正文已在当前成片后修改，请重新合成后再导出工程包"})
    storage_root = settings.storage_path.resolve()
    if final_media is not None:
        final_path = (storage_root / Path(final_media.file_path)).resolve()
        if not final_path.is_relative_to(storage_root) or not final_path.is_file():
            issues.append({"code": "FINAL_FILE_MISSING", "message": "当前整集成片文件不存在"})
    version_ids = [int(value) for value in payload.get("segment_video_version_ids") or []]
    versions = list((await session.scalars(
        select(SegmentVideoVersion).where(SegmentVideoVersion.id.in_(version_ids))
    )).all()) if version_ids else []
    version_by_id = {item.id: item for item in versions}
    segments = list(snapshot.get("segments") or [])
    if snapshot and (not version_ids or len(version_by_id) != len(version_ids) or len(segments) != len(version_ids)):
        issues.append({"code": "SEGMENT_VERSION_MISSING", "message": "成片采用的片段版本不完整"})
    segment_rows = []
    for order, (segment, version_id) in enumerate(zip(segments, version_ids, strict=False), start=1):
        version = version_by_id.get(version_id)
        media = await session.get(MediaFile, version.media_file_id) if version else None
        path = (storage_root / Path(media.file_path)).resolve() if media else None
        available = bool(path and path.is_relative_to(storage_root) and path.is_file())
        if not available:
            issues.append({
                "code": "SEGMENT_MEDIA_MISSING",
                "message": f"片段 {order} 的采用视频文件不存在",
                "segment_id": segment.get("segment_id"),
            })
        segment_rows.append({
            "segment_id": int(segment.get("segment_id") or 0),
            "order": int(segment.get("order") or order),
            "title": segment.get("title"),
            "video_version_id": version_id,
            "media_file_id": media.id if media else None,
            "timeline_duration": float(segment.get("timeline_duration") or 0),
            "trim_in": float(segment.get("trim_in") or 0),
            "available": available,
        })
    fingerprint_payload = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "episode_id": episode.id,
        "episode_script_revision": episode.script_revision,
        "export_job_id": export_job.id if export_job else None,
        "export_snapshot_fingerprint": payload.get("snapshot_fingerprint"),
        "final_media_id": final_media.id if final_media else None,
        "final_media_hash": final_media.hash if final_media else None,
        "segments": segment_rows,
        "pending_edit": pending_edit,
    }
    package_fingerprint = sha256(json.dumps(
        fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {
        "status": "blocked" if issues else "ready",
        "project_id": project_id,
        "project_name": project.name if project else "",
        "episode_id": episode.id,
        "episode_number": episode.number,
        "episode_title": episode.title,
        "export_job_id": export_job.id if export_job else None,
        "final_media_file_id": final_media.id if final_media else None,
        "package_fingerprint": package_fingerprint,
        "export_snapshot_fingerprint": payload.get("snapshot_fingerprint"),
        "output_spec": snapshot.get("output_spec") or {},
        "total_duration": sum(item["timeline_duration"] for item in segment_rows),
        "subtitle_count": len(snapshot.get("subtitle_source") or []),
        "segments": segment_rows,
        "files": ["final/episode.mp4", "subtitles/episode.srt", "scripts/episode.md", "scripts/segments.json", "manifest.json", "checksums.sha256"],
        "issues": issues,
        "_episode": episode,
        "_final_media": final_media,
        "_export_job": export_job,
        "_snapshot": snapshot,
    }


async def create_job(
    session: AsyncSession,
    owner_id: int,
    *,
    project_id: int,
    episode_id: int,
    request_id: str,
    expected_package_fingerprint: str,
) -> Job:
    existing_jobs = list((await session.scalars(select(Job).where(
        owner_scope(Job.owner_id, owner_id),
        Job.project_id == project_id,
        Job.target_type == TARGET_TYPE,
        Job.target_id == episode_id,
    ).order_by(Job.id.desc()))).all())
    for item in existing_jobs:
        if (item.payload or {}).get("request_id") == request_id:
            return item
    if any(item.status not in TERMINAL_STATUSES for item in existing_jobs):
        raise ConflictError("本集已有进行中的工程包导出任务")
    result = await preflight(session, owner_id, project_id=project_id, episode_id=episode_id)
    if result["package_fingerprint"] != expected_package_fingerprint:
        raise ConflictError("工程包来源已变化，请重新预检")
    if result["status"] != "ready":
        raise ConflictError(result["issues"][0]["message"] if result["issues"] else "工程包预检未通过")
    export_job = result["_export_job"]
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type=JOB_TYPE_EXPORT,
        status=JOB_STATUS_QUEUED,
        target_type=TARGET_TYPE,
        target_id=episode_id,
        payload={
            "request_id": request_id,
            "episode_id": episode_id,
            "package_fingerprint": expected_package_fingerprint,
            "export_job_id": export_job.id,
            "final_media_file_id": result["final_media_file_id"],
            "export_snapshot_fingerprint": result["export_snapshot_fingerprint"],
            "segment_video_version_ids": list((export_job.payload or {}).get("segment_video_version_ids") or []),
            "production_snapshot": result["_snapshot"],
            "pricing_snapshot": {
                "status": "estimated", "currency": "CNY", "amount": "0", "estimated_cents": 0,
                "reason": "本地文件打包，不调用外部生成渠道", "pricing_version": "local-package-v1",
            },
        },
        cost_estimate=0,
        max_attempts=1,
    )
    session.add(job)
    await session.flush()
    return job


async def build_package(session: AsyncSession, job: Job) -> MediaFile:
    payload = dict(job.payload or {})
    result = await preflight(
        session, job.owner_id, project_id=int(job.project_id), episode_id=int(payload["episode_id"])
    )
    if result["package_fingerprint"] != payload.get("package_fingerprint") or result["status"] != "ready":
        raise ConflictError("工程包来源已变化或文件缺失，请重新预检")
    episode: Episode = result["_episode"]
    final_media: MediaFile = result["_final_media"]
    snapshot = result["_snapshot"]
    storage_root = settings.storage_path.resolve()
    relative = Path("projects") / str(job.project_id) / "engineering_packages" / f"{uuid4().hex}.zip"
    output = settings.storage_path / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    package_title = _safe_name(episode.title or f"第{episode.number}集", f"episode-{episode.number}")
    root = f"第{episode.number}集-{package_title}"
    try:
        text_files: dict[str, bytes] = {}
        subtitle_lines = []
        for index, item in enumerate(snapshot.get("subtitle_source") or [], start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            subtitle_lines.extend([
                str(index),
                f"{_srt_timestamp(float(item['start_time']))} --> {_srt_timestamp(float(item['end_time']))}",
                text,
                "",
            ])
        text_files["subtitles/episode.srt"] = ("\n".join(subtitle_lines) + ("\n" if subtitle_lines else "")).encode("utf-8-sig")
        script_markdown = f"# 第{episode.number}集 {episode.title or ''}\n\n{episode.script or ''}\n"
        text_files["scripts/episode.md"] = script_markdown.encode("utf-8")
        segment_scripts = [{
            "segment_id": item.get("segment_id"), "order": item.get("order"), "title": item.get("title"),
            "prompt": item.get("prompt"), "negative_prompt": item.get("negative_prompt"),
            "timeline_duration": item.get("timeline_duration"), "trim_in": item.get("trim_in"), "trim_out": item.get("trim_out"),
        } for item in snapshot.get("segments") or []]
        text_files["scripts/segments.json"] = json.dumps(segment_scripts, ensure_ascii=False, indent=2).encode("utf-8")
        media_entries: list[tuple[str, Path, dict[str, Any]]] = []
        final_path = (storage_root / Path(final_media.file_path)).resolve()
        media_entries.append(("final/episode.mp4", final_path, {"media_file_id": final_media.id, "role": "final"}))
        version_ids = [int(value) for value in payload.get("segment_video_version_ids") or []]
        versions = list((await session.scalars(select(SegmentVideoVersion).where(
            SegmentVideoVersion.id.in_(version_ids)
        ))).all())
        by_id = {item.id: item for item in versions}
        timeline_cursor = 0.0
        manifest_segments = []
        for fallback_order, (segment, version_id) in enumerate(zip(snapshot.get("segments") or [], version_ids, strict=True), start=1):
            version = by_id[version_id]
            media = await session.get(MediaFile, version.media_file_id)
            source = (storage_root / Path(media.file_path)).resolve()
            order = int(segment.get("order") or fallback_order)
            filename = f"segments/{order:03d}-{_safe_name(str(segment.get('title') or '片段'), 'segment')}.mp4"
            media_entries.append((filename, source, {"media_file_id": media.id, "role": "segment"}))
            duration = float(segment.get("timeline_duration") or 0)
            manifest_segments.append({
                "segment_id": segment.get("segment_id"), "order": order, "title": segment.get("title"),
                "video_version_id": version.id, "media_file_id": media.id, "path": filename,
                "timeline_start": timeline_cursor, "timeline_end": timeline_cursor + duration,
                "timeline_duration": duration, "trim_in": float(segment.get("trim_in") or 0),
                "source_duration": float(segment.get("generation_duration") or duration),
            })
            timeline_cursor += duration
        file_records = []
        for path, data in text_files.items():
            file_records.append({"path": path, "size": len(data), "sha256": sha256(data).hexdigest(), "role": "metadata"})
        for archive_path, source, metadata in media_entries:
            file_records.append({"path": archive_path, "size": source.stat().st_size, "sha256": _digest_file(source), **metadata})
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "package_fingerprint": payload["package_fingerprint"],
            "export_snapshot_fingerprint": payload.get("export_snapshot_fingerprint"),
            "project": {"id": job.project_id, "name": result["project_name"]},
            "episode": {"id": episode.id, "number": episode.number, "title": episode.title, "script_revision": episode.script_revision},
            "output_spec": snapshot.get("output_spec") or {},
            "audio_mix_order": snapshot.get("audio_mix_order") or [],
            "dialogue_duplicate_audio_policy": snapshot.get("dialogue_duplicate_audio_policy") or {},
            "segments": manifest_segments,
            "files": file_records,
            "compatibility": {
                "portable_relative_paths": True,
                "editable_segment_sequence": True,
                "supported": ["video", "audio", "subtitles", "script", "timing", "checksums"],
                "not_included": ["provider_credentials", "temporary_tokens", "platform_specific_effects"],
            },
        }
        manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        text_files["manifest.json"] = manifest_data
        checksums = [f"{item['sha256']}  {item['path']}" for item in sorted(file_records, key=lambda row: row["path"])]
        checksums.append(f"{sha256(manifest_data).hexdigest()}  manifest.json")
        text_files["checksums.sha256"] = ("\n".join(checksums) + "\n").encode("ascii")
        with ZipFile(output, "w", allowZip64=True) as archive:
            for path, data in text_files.items():
                archive.writestr(f"{root}/{path}", data, compress_type=ZIP_DEFLATED)
            for archive_path, source, _metadata in media_entries:
                archive.write(source, f"{root}/{archive_path}", compress_type=ZIP_STORED)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    size = output.stat().st_size
    digest = _digest_file(output)
    media = MediaFile(
        project_id=job.project_id, owner_id=job.owner_id, kind="file", source="export",
        file_path=relative.as_posix(), original_name=f"第{episode.number}集-{package_title}-工程素材包.zip",
        mime_type="application/zip", size=size, hash=digest,
    )
    session.add(media)
    await session.flush()
    session.add(ProjectMediaLink(project_id=int(job.project_id), media_file_id=media.id))
    await session.flush()
    return media


async def list_versions(session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int) -> list[dict[str, Any]]:
    await _episode(session, owner_id, project_id, episode_id)
    jobs = list((await session.scalars(select(Job).where(
        owner_scope(Job.owner_id, owner_id), Job.project_id == project_id,
        Job.target_type == TARGET_TYPE, Job.target_id == episode_id, Job.status == JOB_STATUS_SUCCEEDED,
    ).order_by(Job.id))).all())
    versions = []
    for version, job in enumerate(jobs, start=1):
        media_id = int((job.result or {}).get("media_file_id") or 0)
        media = await session.get(MediaFile, media_id) if media_id else None
        path = (settings.storage_path.resolve() / Path(media.file_path)).resolve() if media else None
        versions.append({
            "version": version, "job_id": job.id, "media_file_id": media_id,
            "original_name": media.original_name if media else None,
            "size": media.size if media else None, "hash": media.hash if media else None,
            "created_at": job.finished_at or job.created_at,
            "available": bool(path and path.is_relative_to(settings.storage_path.resolve()) and path.is_file()),
            "package_fingerprint": (job.payload or {}).get("package_fingerprint"),
        })
    return list(reversed(versions))
