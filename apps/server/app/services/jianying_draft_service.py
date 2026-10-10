"""Isolated Jianying Pro 11.4 draft packages for R11-C."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
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
from app.services import engineering_package_service, premiere_xml_service
from app.services.jianying_diagnostics import DRAFT_PATH_TOKEN as DRAFT_PATH_TOKEN
from app.services.jianying_diagnostics import DRAFT_ROOT_TOKEN as DRAFT_ROOT_TOKEN
from app.services.jianying_diagnostics import INSTALLER_FILENAME as INSTALLER_FILENAME
from app.services.jianying_diagnostics import INSTALL_README_FILENAME as INSTALL_README_FILENAME
from app.services.jianying_diagnostics import MEDIA_ROOT_TOKEN as MEDIA_ROOT_TOKEN
from app.services.jianying_diagnostics import REFERENCE_VALIDATION as REFERENCE_VALIDATION
from app.services.jianying_diagnostics import RENAME_WARNING as RENAME_WARNING
from app.services.jianying_diagnostics import SCHEMA_VERSION as SCHEMA_VERSION
from app.services.jianying_diagnostics import TARGET_APP as TARGET_APP
from app.services.jianying_diagnostics import TARGET_TYPE as TARGET_TYPE
from app.services.jianying_diagnostics import TARGET_VERSION as TARGET_VERSION
from app.services.jianying_diagnostics import _local_draft_root as _local_draft_root
from app.services.jianying_diagnostics import installation_diagnostics as installation_diagnostics
from app.services.jianying_draft_content import build_draft_content as build_draft_content
from app.services.jianying_draft_content import compatibility_metadata as compatibility_metadata
from app.services.jianying_installer import _digest_file as _digest_file
from app.services.jianying_installer import _id as _id
from app.services.jianying_installer import _safe_name as _safe_name
from app.services.jianying_installer import _token_path as _token_path
from app.services.jianying_installer import build_installer_script as build_installer_script
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.team_access import owner_scope


async def preflight(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int
) -> dict[str, Any]:
    base = await engineering_package_service.preflight(
        session, owner_id, project_id=project_id, episode_id=episode_id
    )
    source_job = base.get("_export_job")
    source_payload = dict(source_job.payload or {}) if source_job else {}
    audio, audio_issues = await premiere_xml_service._audio_sources(
        session,
        owner_id=owner_id,
        payload=source_payload,
        snapshot=base.get("_snapshot") or {},
    )
    issues = [*base["issues"], *audio_issues]
    draft_root = _local_draft_root()
    installation = installation_diagnostics()
    if settings.runtime_execution_location == "local" and (draft_root is None or not draft_root.is_dir()):
        issues.append(
            {
                "code": "JIANYING_DRAFT_ROOT_MISSING",
                "message": "未检测到本机剪映专业版草稿目录",
            }
        )
    fingerprint = sha256(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "target_version": TARGET_VERSION,
                "source_package_fingerprint": base["package_fingerprint"],
                "audio": [
                    {
                        "media_id": item["media_id"],
                        "hash": item["media"].hash,
                        "role": item["role"],
                        "start_time": item["start_time"],
                        "end_time": item["end_time"],
                        "gain": item["gain"],
                        "loop": item["loop"],
                        "audio_mode": item.get("audio_mode"),
                    }
                    for item in audio
                ],
                "subtitles": base.get("_snapshot", {}).get("subtitle_source") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        **{key: value for key, value in base.items() if not key.startswith("_")},
        "package_fingerprint": fingerprint,
        "source_package_fingerprint": base["package_fingerprint"],
        "target_app": TARGET_APP,
        "target_version": TARGET_VERSION,
        "installation": installation,
        "status": "blocked" if issues else "ready",
        "issues": issues,
        "audio_count": len(audio),
        "subtitle_count": len(base.get("_snapshot", {}).get("subtitle_source") or []),
        "install_root": str(draft_root) if draft_root else None,
        "files": [
            "draft_content.json",
            "draft_meta_info.json",
            "Timelines/project.json",
            "Timelines/<id>/draft_content.json",
            "videos/*.mp4",
            "audios/*",
            INSTALLER_FILENAME,
            INSTALL_README_FILENAME,
            "compatibility.json",
            "manifest.json",
            "checksums.sha256",
        ],
        "_base": base,
        "_audio": audio,
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
    existing = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == TARGET_TYPE,
                    Job.target_id == episode_id,
                )
                .order_by(Job.id.desc())
            )
        ).all()
    )
    for item in existing:
        if (item.payload or {}).get("request_id") == request_id:
            return item
    if any(item.status not in TERMINAL_STATUSES for item in existing):
        raise ConflictError("本集已有进行中的剪映草稿导出任务")
    result = await preflight(
        session, owner_id, project_id=project_id, episode_id=episode_id
    )
    if result["package_fingerprint"] != expected_package_fingerprint:
        raise ConflictError("剪映草稿来源已变化, 请重新预检")
    if result["status"] != "ready":
        raise ConflictError(
            result["issues"][0]["message"]
            if result["issues"]
            else "剪映草稿预检未通过"
        )
    source_job = result["_base"]["_export_job"]
    source_payload = dict(source_job.payload or {})
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type=JOB_TYPE_EXPORT,
        status=JOB_STATUS_QUEUED,
        target_type=TARGET_TYPE,
        target_id=episode_id,
        max_attempts=1,
        cost_estimate=0,
        payload={
            "request_id": request_id,
            "episode_id": episode_id,
            "package_fingerprint": expected_package_fingerprint,
            "source_package_fingerprint": result["source_package_fingerprint"],
            "export_job_id": source_job.id,
            "export_snapshot_fingerprint": result["export_snapshot_fingerprint"],
            "segment_video_version_ids": list(
                source_payload.get("segment_video_version_ids") or []
            ),
            "production_snapshot": result["_base"]["_snapshot"],
            "pricing_snapshot": {
                "status": "estimated",
                "currency": "CNY",
                "amount": "0",
                "estimated_cents": 0,
                "reason": "本地生成剪映草稿包, 不调用外部渠道",
                "pricing_version": "local-jianying-draft-v1",
            },
        },
    )
    session.add(job)
    await session.flush()
    return job


async def build_package(session: AsyncSession, job: Job) -> MediaFile:
    payload = dict(job.payload or {})
    result = await preflight(
        session,
        job.owner_id,
        project_id=int(job.project_id),
        episode_id=int(payload["episode_id"]),
    )
    if (
        result["status"] != "ready"
        or result["package_fingerprint"] != payload.get("package_fingerprint")
    ):
        raise ConflictError("剪映草稿来源已变化或文件缺失, 请重新预检")
    base, snapshot = result["_base"], result["_base"]["_snapshot"]
    episode = base["_episode"]
    spec = snapshot.get("output_spec") or {}
    width, height = int(spec.get("width") or 1280), int(spec.get("height") or 720)
    draft_id = _id()
    root_name = _safe_name(
        f"第{episode.number}集-{episode.title or '未命名'}-Works草稿-{draft_id[:8]}",
        f"episode-{episode.number}-draft",
    )
    install_root = _local_draft_root()
    if install_root is None:
        raise ConflictError("未检测到本机剪映专业版草稿目录")
    storage_root = settings.storage_path.resolve()
    relative = (
        Path("projects")
        / str(job.project_id)
        / "jianying_drafts"
        / f"{uuid4().hex}.zip"
    )
    output = settings.storage_path / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    version_ids = [
        int(value) for value in payload.get("segment_video_version_ids") or []
    ]
    versions = list(
        (
            await session.scalars(
                select(SegmentVideoVersion).where(
                    SegmentVideoVersion.id.in_(version_ids)
                )
            )
        ).all()
    )
    version_by_id = {item.id: item for item in versions}
    draft_segments, media_entries, manifest_segments = [], [], []
    for fallback, (item, version_id) in enumerate(
        zip(snapshot.get("segments") or [], version_ids, strict=True), start=1
    ):
        version = version_by_id[version_id]
        media = await session.get(MediaFile, version.media_file_id)
        source = (storage_root / Path(media.file_path)).resolve()
        order = int(item.get("order") or fallback)
        filename = (
            f"{order:03d}-"
            f"{_safe_name(str(item.get('title') or '片段'), 'segment')}.mp4"
        )
        archive_path = f"videos/{filename}"
        duration_us = round(float(item.get("timeline_duration") or 0) * 1_000_000)
        trim_in_us = round(float(item.get("trim_in") or 0) * 1_000_000)
        source_duration_us = max(
            round(
                float(item.get("generation_duration") or media.duration or 0)
                * 1_000_000
            ),
            trim_in_us + duration_us,
        )
        draft_segments.append(
            {
                "draft_path": _token_path(MEDIA_ROOT_TOKEN, archive_path),
                "duration_us": duration_us,
                "trim_in_us": trim_in_us,
                "source_duration_us": source_duration_us,
            }
        )
        media_entries.append((archive_path, source, media.id, "segment"))
        manifest_segments.append(
            {
                "order": order,
                "segment_id": item.get("segment_id"),
                "video_version_id": version.id,
                "media_file_id": media.id,
                "path": archive_path,
                "duration_us": duration_us,
                "trim_in_us": trim_in_us,
            }
        )
    audio_clips: list[dict[str, Any]] = []
    manifest_audio: list[dict[str, Any]] = []
    copied_audio: dict[int, str] = {}
    for index, item in enumerate(result["_audio"], start=1):
        media: MediaFile = item["media"]
        if media.id not in copied_audio:
            suffix = (
                Path(media.original_name or media.file_path).suffix
                or Path(media.file_path).suffix
                or ".audio"
            )
            stem = _safe_name(
                Path(media.original_name or f"audio-{media.id}").stem,
                f"audio-{media.id}",
            )
            copied_audio[media.id] = f"audios/{index:03d}-{stem}{suffix.lower()}"
            media_entries.append(
                (copied_audio[media.id], item["source"], media.id, "audio")
            )
        start_us = max(round(float(item["start_time"]) * 1_000_000), 0)
        end_us = max(round(float(item["end_time"]) * 1_000_000), start_us)
        target_duration_us = end_us - start_us
        source_duration_us = max(
            round(float(media.duration or target_duration_us / 1_000_000) * 1_000_000),
            1,
        )
        cursor_us = start_us
        remaining_us = target_duration_us
        clip_count = 0
        while remaining_us > 0:
            duration_us = min(remaining_us, source_duration_us)
            audio_clips.append(
                {
                    "draft_path": _token_path(
                        MEDIA_ROOT_TOKEN, copied_audio[media.id]
                    ),
                    "name": item["name"],
                    "role": item["role"],
                    "start_us": cursor_us,
                    "source_start_us": 0,
                    "duration_us": duration_us,
                    "source_duration_us": source_duration_us,
                    "gain": item["gain"],
                }
            )
            clip_count += 1
            cursor_us += duration_us
            remaining_us -= duration_us
            if not item["loop"]:
                break
        manifest_audio.append(
            {
                "media_file_id": media.id,
                "path": copied_audio[media.id],
                "name": item["name"],
                "role": item["role"],
                "start_us": start_us,
                "end_us": end_us,
                "gain": item["gain"],
                "loop": item["loop"],
                "audio_mode": item.get("audio_mode"),
                "clip_count": clip_count,
            }
        )
    subtitles = [
        {
            "text": str(item.get("text") or "").strip(),
            "start_us": max(
                round(float(item.get("start_time") or 0) * 1_000_000), 0
            ),
            "end_us": max(
                round(float(item.get("end_time") or 0) * 1_000_000), 0
            ),
        }
        for item in snapshot.get("subtitle_source") or []
        if str(item.get("text") or "").strip()
        and float(item.get("end_time") or 0)
        > float(item.get("start_time") or 0)
    ]
    native_mute_ranges = [
        {
            "start_us": max(round(float(item["start_time"]) * 1_000_000), 0),
            "end_us": max(round(float(item["end_time"]) * 1_000_000), 0),
            "source": item["name"],
        }
        for item in result["_audio"]
        if item["role"] == "dialogue"
        and item.get("audio_mode", "replace") == "replace"
        and float(item["end_time"]) > float(item["start_time"])
    ]
    content = build_draft_content(
        draft_id=draft_id,
        width=width,
        height=height,
        segments=draft_segments,
        audio_clips=audio_clips,
        subtitles=subtitles,
        native_mute_ranges=native_mute_ranges,
    )
    now_us = int(datetime.now(UTC).timestamp() * 1_000_000)
    project = {
        "config": {
            "color_space": -1,
            "mixed_track_mode_on": False,
            "render_index_track_mode_on": True,
            "use_float_render": False,
        },
        "create_time": now_us,
        "id": draft_id,
        "main_timeline_id": draft_id,
        "timelines": [
            {
                "create_time": now_us,
                "id": draft_id,
                "is_marked_delete": False,
                "name": "时间线01",
                "update_time": now_us,
            }
        ],
        "update_time": now_us,
        "version": 0,
    }
    meta = {
        "draft_id": draft_id,
        "draft_name": root_name,
        "draft_fold_path": DRAFT_PATH_TOKEN,
        "draft_root_path": DRAFT_ROOT_TOKEN,
        "draft_timeline_materials_size_": sum(
            source.stat().st_size for _, source, _, _ in media_entries
        ),
        "tm_duration": content["duration"],
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "draft_version": content["version"],
    }
    compatibility = compatibility_metadata(
        DRAFT_PATH_TOKEN,
        stable_media_root=MEDIA_ROOT_TOKEN,
    )
    content_data = json.dumps(
        content, ensure_ascii=False, separators=(",", ":")
    ).encode()
    text: dict[str, bytes] = {
        "draft_content.json": content_data,
        "draft_meta_info.json": json.dumps(
            meta, ensure_ascii=False, separators=(",", ":")
        ).encode(),
        "Timelines/project.json": json.dumps(
            project, ensure_ascii=False, separators=(",", ":")
        ).encode(),
        f"Timelines/{draft_id}/draft_content.json": content_data,
        "compatibility.json": json.dumps(
            compatibility, ensure_ascii=False, indent=2
        ).encode(),
        INSTALLER_FILENAME: build_installer_script(
            draft_id=draft_id,
            root_name=root_name,
        ),
        INSTALL_README_FILENAME: (
            "剪映专业版草稿安装说明\r\n"
            "\r\n"
            "1. 保持本目录结构完整, 不要直接复制到剪映草稿目录。\r\n"
            f"2. 右键 {INSTALLER_FILENAME}, 使用 PowerShell 运行。\r\n"
            "3. 安装器会先校验下载包, 再分别安装草稿与稳定媒体。\r\n"
            "4. 安装器不会覆盖任何已有草稿或 WorksMedia 目录。\r\n"
            "5. 安装成功后启动剪映; 此时可在剪映内重命名草稿。\r\n"
            "\r\n"
            "注意: checksums.sha256 校验的是安装前下载包。安装时 JSON "
            "路径令牌会被改写为本机绝对路径, 因此安装后的 JSON 哈希会变化。\r\n"
        ).encode(),
    }
    records = [
        {
            "path": path,
            "size": len(data),
            "sha256": sha256(data).hexdigest(),
            "role": "metadata",
        }
        for path, data in text.items()
    ]
    records.extend(
        {
            "path": path,
            "size": source.stat().st_size,
            "sha256": _digest_file(source),
            "role": role,
            "media_file_id": media_id,
        }
        for path, source, media_id, role in media_entries
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "package_fingerprint": payload["package_fingerprint"],
        "source_package_fingerprint": payload["source_package_fingerprint"],
        "target": {"app": TARGET_APP, "version": TARGET_VERSION},
        "draft": {
            "id": draft_id,
            "name": root_name,
            "required_install_path": DRAFT_PATH_TOKEN,
            "stable_media_path": MEDIA_ROOT_TOKEN,
            "duration_us": content["duration"],
            "width": width,
            "height": height,
        },
        "segments": manifest_segments,
        "audio": manifest_audio,
        "native_audio_mutes": native_mute_ranges,
        "subtitles": subtitles,
        "files": records,
        "validation": {
            "json_structure": "passed",
            "media_references": "passed",
            "jianying_application": "not_run",
            "artifact_application": "not_run",
            "reference_jianying_application": "passed_with_limitations",
            "native_dialogue_replace_mute": (
                "structural_passed_application_not_run"
                if native_mute_ranges
                else "not_required"
            ),
        },
        "installation": {
            "installer_required": True,
            "installer": INSTALLER_FILENAME,
            "overwrite_existing": False,
            "rename_safe_after_install": True,
            "checksums_verified_before_json_rewrite": True,
        },
    }
    manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    text["manifest.json"] = manifest_data
    checksums = [
        f"{item['sha256']}  {item['path']}"
        for item in sorted(records, key=lambda row: row["path"])
    ]
    checksums.append(f"{sha256(manifest_data).hexdigest()}  manifest.json")
    text["checksums.sha256"] = ("\n".join(checksums) + "\n").encode()
    try:
        with ZipFile(output, "w", allowZip64=True) as archive:
            for path, data in text.items():
                archive.writestr(
                    f"{root_name}/{path}", data, compress_type=ZIP_DEFLATED
                )
            for path, source, _, _ in media_entries:
                archive.write(
                    source, f"{root_name}/{path}", compress_type=ZIP_STORED
                )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    media = MediaFile(
        project_id=job.project_id,
        owner_id=job.owner_id,
        kind="file",
        source="export",
        file_path=relative.as_posix(),
        original_name=f"{root_name}-剪映11.4草稿.zip",
        mime_type="application/zip",
        size=output.stat().st_size,
        hash=_digest_file(output),
    )
    session.add(media)
    await session.flush()
    session.add(
        ProjectMediaLink(project_id=int(job.project_id), media_file_id=media.id)
    )
    await session.flush()
    return media


async def list_versions(
    session: AsyncSession, owner_id: int, *, project_id: int, episode_id: int
) -> list[dict[str, Any]]:
    await engineering_package_service._episode(
        session, owner_id, project_id, episode_id
    )
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(
                    owner_scope(Job.owner_id, owner_id),
                    Job.project_id == project_id,
                    Job.target_type == TARGET_TYPE,
                    Job.target_id == episode_id,
                    Job.status == JOB_STATUS_SUCCEEDED,
                )
                .order_by(Job.id)
            )
        ).all()
    )
    root, versions = settings.storage_path.resolve(), []
    for version, job in enumerate(jobs, start=1):
        media_id = int((job.result or {}).get("media_file_id") or 0)
        media = await session.get(MediaFile, media_id) if media_id else None
        path = (root / Path(media.file_path)).resolve() if media else None
        versions.append(
            {
                "version": version,
                "job_id": job.id,
                "media_file_id": media_id,
                "original_name": media.original_name if media else None,
                "size": media.size if media else None,
                "hash": media.hash if media else None,
                "created_at": job.finished_at or job.created_at,
                "available": bool(
                    path and path.is_relative_to(root) and path.is_file()
                ),
                "package_fingerprint": (job.payload or {}).get(
                    "package_fingerprint"
                ),
                "application_validation": "not_run",
                "target_version": TARGET_VERSION,
            }
        )
    return list(reversed(versions))
