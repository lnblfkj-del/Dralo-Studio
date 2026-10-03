"""Read-only local recognition tasks; applying results uses the ordinary edit receipt."""
# ruff: noqa: RUF001

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import PROJECT_ROOT, settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.models import Job, MediaFile
from app.models.edit_project import EditProject
from app.services import job_service, worker_runtime_service
from app.services.canvas_processing_service import command, executables, file_path, probe
from app.services.edit_project_asr_projection import project_captions, subtitle_commands
from app.services.edit_project_service import get_edit_project
from app.services.edit_project_source_service import verify_project_sources

TARGET = "edit_project_asr"
RUNNER = PROJECT_ROOT / "scripts" / "runtime" / "multitrack-asr.mjs"


def runtime_status():
    model = settings.asr_model_path / "onnx-community" / "whisper-small"
    required = [
        "config.json",
        "generation_config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "normalizer.json",
        "onnx/encoder_model_quantized.onnx",
        "onnx/decoder_model_merged_quantized.onnx",
    ]
    ready = bool(
        shutil.which(settings.asr_node_path)
        and RUNNER.is_file()
        and (
            settings.asr_runtime_path / "node_modules/@huggingface/transformers/package.json"
        ).is_file()
        and all((model / file).is_file() for file in required)
        and (settings.asr_runtime_path / "model-metadata.json").is_file()
    )
    return {
        "ready": ready,
        "engine": "whisper-small-q8",
        "message": "" if ready else "本地字幕识别运行时尚未安装，请完成 ASR 运行时准备。",
    }


async def create(session, *, project_id, edit_project_id, owner_id, payload):
    project = await get_edit_project(
        session, project_id=project_id, edit_project_id=edit_project_id, owner_id=owner_id
    )
    query = select(Job).where(
        Job.project_id == project_id,
        Job.target_type == TARGET,
        Job.target_id == edit_project_id,
    )
    previous = await session.scalar(
        query.where(
            Job.owner_id == owner_id, Job.payload["request_id"].as_string() == payload.request_id
        )
    )
    public_payload = payload.model_dump(mode="json")
    if previous:
        if previous.payload.get("request") != public_payload:
            raise ConflictError("识别请求标识已用于其他参数")
        return previous
    if not runtime_status()["ready"]:
        raise ConflictError(runtime_status()["message"])
    runtime = await worker_runtime_service.status(session, include_workers=True)
    if not any(
        "media_process" in item["capabilities"]
        for item in runtime.get("workers", [])
        if item["version"] == worker_runtime_service.RUNTIME_VERSION
    ):
        raise ConflictError("字幕识别 Worker 未在线，尚未提交任务")
    if (
        project.revision != payload.expected_revision
        or project.fingerprint != payload.expected_fingerprint
    ):
        raise ConflictError("剪辑工程已变化，请保存并重新核对")
    clip = (
        next((clip for clip in project.document.clips if clip.clip_id == payload.clip_id), None)
        if project.document
        else None
    )
    if clip is None or clip.track not in {"video", "dialogue"}:
        raise ConflictError("请选择已保存的视频或对白音轨，不能识别 BGM 或环境音")
    if clip.duration_frames > project.frame_rate * 600:
        raise ConflictError("字幕识别支持最长 10 分钟的音轨，请先裁切")
    if clip.track == "dialogue" and clip.audio_fill == "loop":
        raise ConflictError("循环音轨不能作为字幕识别来源")
    # Serialize task creation on the edit identity without changing its revision/document.
    locked = await session.execute(
        update(EditProject)
        .where(
            EditProject.id == edit_project_id,
            EditProject.revision == project.revision,
            EditProject.fingerprint == project.fingerprint,
        )
        .values(revision=project.revision)
    )
    if locked.rowcount != 1:
        raise ConflictError("剪辑工程已变化，请重新核对")
    previous = await session.scalar(
        query.where(
            Job.owner_id == owner_id, Job.payload["request_id"].as_string() == payload.request_id
        )
    )
    if previous:
        if previous.payload.get("request") != public_payload:
            raise ConflictError("识别请求标识已用于其他参数")
        return previous
    active = await session.scalar(
        query.where(Job.deleted_at.is_(None), Job.status.not_in(job_service.TERMINAL_STATUSES))
    )
    if active:
        raise ConflictError("该工程已有字幕识别任务")
    row = await session.get(EditProject, edit_project_id)
    await verify_project_sources(
        session,
        project_id=project_id,
        owner_id=owner_id,
        document=project.document.model_copy(update={"clips": [clip]}),
        expected_evidence=row.source_evidence,
    )
    job = Job(
        owner_id=owner_id,
        project_id=project_id,
        job_type="media_process",
        target_type=TARGET,
        target_id=edit_project_id,
        max_attempts=1,
        cost_estimate=0,
        model="whisper-small-q8",
        provider="local",
        payload={
            "request_id": payload.request_id,
            "request": public_payload,
            "clip": clip.model_dump(mode="json"),
            "frame_rate": project.frame_rate,
            "source_evidence": {
                str(clip.media_file_id): row.source_evidence[str(clip.media_file_id)]
            },
        },
    )
    session.add(job)
    await session.flush()
    return job


async def preflight(session, job):
    project = await get_edit_project(
        session, project_id=job.project_id, edit_project_id=job.target_id, owner_id=job.owner_id
    )
    if project.fingerprint != job.payload["request"]["expected_fingerprint"]:
        raise ConflictError("剪辑工程已变化，请重新创建识别任务")
    clip = (
        next(
            (
                clip
                for clip in project.document.clips
                if clip.clip_id == job.payload["request"]["clip_id"]
            ),
            None,
        )
        if project.document
        else None
    )
    if clip is None or clip.model_dump(mode="json") != job.payload["clip"]:
        raise ConflictError("识别来源或裁切区间已变化")
    await verify_project_sources(
        session,
        project_id=job.project_id,
        owner_id=job.owner_id,
        document=project.document.model_copy(update={"clips": [clip]}),
        expected_evidence=job.payload["source_evidence"],
    )
    return project, clip


async def expand_result(session, project, document, command):
    job = await session.get(Job, command.job_id)
    if (
        not job
        or job.deleted_at
        or job.project_id != project.project_id
        or job.target_id != project.id
        or job.target_type != TARGET
    ):
        raise NotFoundError("字幕识别任务不存在")
    if job.status != "succeeded" or not job.result:
        raise ConflictError("字幕识别任务尚未成功")
    if job.payload["request"]["expected_fingerprint"] != project.fingerprint:
        raise ConflictError("识别后工程已经变化，请重新识别，旧结果未应用")
    source = next(
        (clip for clip in document.clips if clip.clip_id == job.payload["request"]["clip_id"]), None
    )
    if source is None or source.model_dump(mode="json") != job.payload["clip"]:
        raise ConflictError("识别来源在当前保存中已变化，请重新识别")
    return subtitle_commands(
        document,
        job_id=job.id,
        fingerprint=project.fingerprint,
        source_clip_id=job.payload["request"]["clip_id"],
        captions=job.result["captions"],
        replace=command.replace_automatic,
    )


async def execute(job_id, worker_id):
    async def active():
        async with SessionLocal() as session:
            value = await job_service.renew_lease(session, job_id, worker_id)
            await session.commit()
            return value

    async with SessionLocal() as session:
        if not await job_service.mark_processing(session, job_id, worker_id):
            return
        job = await session.get(Job, job_id)
        project, clip = await preflight(session, job)
        source = file_path(await session.get(MediaFile, clip.media_file_id))
        fps, language = project.frame_rate, job.payload["request"]["language"]
        await session.commit()
    if not runtime_status()["ready"]:
        raise ConflictError(runtime_status()["message"])
    metadata = await probe(source, active)
    duration = min(
        (clip.source_out_frame - clip.source_in_frame) / fps,
        metadata["duration"] - clip.source_in_frame / fps,
    )
    if duration <= 0 or duration > 600:
        raise ConflictError("字幕识别支持最长 10 分钟的有效音轨")
    captions = []
    if metadata["has_audio"]:
        ffmpeg, _ = executables()
        with tempfile.TemporaryDirectory(
            prefix=".edit-asr-", dir=settings.storage_path
        ) as directory:
            audio = Path(directory) / "audio.f32"
            output = Path(directory) / "captions.json"
            await command(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-protocol_whitelist",
                    "file",
                    "-ss",
                    str(clip.source_in_frame / fps),
                    "-i",
                    str(source),
                    "-t",
                    str(duration),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "f32le",
                    str(audio),
                ],
                timeout=60,
                active=active,
                output=audio,
            )
            await command(
                [
                    shutil.which(settings.asr_node_path),
                    str(RUNNER),
                    str(settings.asr_runtime_path),
                    str(settings.asr_model_path),
                    str(audio),
                    str(output),
                    language,
                ],
                timeout=settings.asr_timeout_seconds,
                active=active,
                output=output,
            )
            raw = json.loads(output.read_text(encoding="utf-8"))
            captions = project_captions(raw["chunks"], round(duration * fps), fps, speed=clip.speed)
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        await preflight(session, job)
        await job_service.mark_succeeded(
            session,
            job_id,
            worker_id,
            {
                "captions": captions,
                "engine": "whisper-small-q8",
                "language": language,
                "source_clip_id": clip.clip_id,
                "source_fingerprint": project.fingerprint,
            },
        )
        await session.commit()
