"""R8 candidate evidence and adoption validation for segment videos."""

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.models import EpisodeProductionPlan, Job, MediaFile, SegmentScriptSnapshot, SegmentVideoVersion, VideoSegment
from app.services.asset_production_service import fingerprint
from app.services.production_snapshot_service import build_script_snapshot_content, generation_content


EVIDENCE_KEY = "segment_video_candidate"


def build_input_evidence(job: Job) -> dict[str, Any]:
    payload = job.payload or {}
    snapshot = {
        "schema_version": 1,
        "segment_id": job.target_id,
        "plan_id": payload.get("plan_id"),
        "provider_model_id": payload.get("provider_model_id"),
        "script_snapshot_id": payload.get("script_snapshot_id"),
        "script_fingerprint": payload.get("script_fingerprint"),
        "first_frame_media_id": payload.get("first_frame_media_id"),
        "last_frame_media_id": payload.get("last_frame_media_id"),
        "reference_media_ids": payload.get("reference_media_ids", []),
        "asset_bindings": payload.get("asset_bindings", []),
        "video_input_contract": payload.get("video_input_contract"),
        "continuity_dependency": payload.get("continuity_dependency"),
        "sound_input": payload.get("sound_input"),
        "parameters": payload.get("parameters", {}),
    }
    return {"snapshot": snapshot, "fingerprint": fingerprint(snapshot)}


def attach_input_evidence(job: Job) -> None:
    evidence = build_input_evidence(job)
    job.payload = {
        **(job.payload or {}),
        "video_input_snapshot": evidence["snapshot"],
        "video_input_fingerprint": evidence["fingerprint"],
    }


def version_parameters(job: Job) -> dict[str, Any]:
    payload = job.payload or {}
    return {
        **(payload.get("parameters") or {}),
        EVIDENCE_KEY: {
            "schema_version": 1,
            "script_snapshot_id": payload.get("script_snapshot_id"),
            "script_fingerprint": payload.get("script_fingerprint"),
            "input_fingerprint": payload.get("video_input_fingerprint"),
            "input_snapshot": payload.get("video_input_snapshot"),
        },
    }


def _media_state(media: MediaFile | None) -> tuple[str, str | None]:
    if media is None:
        return "missing", "候选媒体记录不存在"
    path = (settings.storage_path.resolve() / Path(media.file_path)).resolve()
    if not path.is_relative_to(settings.storage_path.resolve()) or not path.is_file():
        return "missing", "候选视频文件不存在"
    if media.kind != "video" or not str(media.mime_type or "").startswith("video/"):
        return "invalid", "候选媒体不是视频文件"
    if not media.width or not media.height or media.duration is None or media.duration <= 0:
        return "invalid", "候选视频尚未通过可播放性校验"
    return "ready", None


async def candidate_evidence(
    session: AsyncSession,
    *,
    segment: VideoSegment,
    plan: EpisodeProductionPlan,
    version: SegmentVideoVersion,
) -> dict[str, Any]:
    media = await session.get(MediaFile, version.media_file_id)
    media_status, reason = _media_state(media)
    source_job = await session.get(Job, version.source_job_id) if version.source_job_id else None
    stored = (version.parameters or {}).get(EVIDENCE_KEY)
    if not isinstance(stored, dict):
        return {
            "status": "legacy_unverified",
            "adoptable": False,
            "reason": reason or "历史候选缺少R8冻结证据，不能直接采用",
            "media_status": media_status,
            "input_fingerprint": None,
            "script_fingerprint": None,
            "provider_model_id": None,
        }
    input_snapshot = stored.get("input_snapshot")
    input_fingerprint = stored.get("input_fingerprint")
    if not isinstance(input_snapshot, dict) or not isinstance(input_fingerprint, str) or fingerprint(input_snapshot) != input_fingerprint:
        return {
            "status": "evidence_invalid",
            "adoptable": False,
            "reason": "候选冻结证据不完整或已损坏",
            "media_status": media_status,
            "input_fingerprint": input_fingerprint if isinstance(input_fingerprint, str) else None,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id") if isinstance(input_snapshot, dict) else None,
        }
    if media_status != "ready":
        return {
            "status": "media_unavailable",
            "adoptable": False,
            "reason": reason,
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    if source_job is None or source_job.status != "succeeded":
        return {
            "status": "evidence_invalid",
            "adoptable": False,
            "reason": "候选生成任务记录缺失或尚未成功完成",
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    from app.services.segment_candidate_reuse_service import REUSE_KEY, reusable_inputs
    reused = False
    if (version.parameters or {}).get(REUSE_KEY):
        reused = await reusable_inputs(session, segment, plan, version)
    if (input_snapshot.get("segment_id") != segment.id or input_snapshot.get("plan_id") != plan.id) and not reused:
        return {
            "status": "input_stale",
            "adoptable": False,
            "reason": "历史视频已保留，可继续预览；对应脚本或生成设置与当前片段不同，不能直接采用",
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    if input_snapshot.get("provider_model_id") != plan.provider_model_id:
        return {
            "status": "input_stale",
            "adoptable": False,
            "reason": "候选使用的视频模型与当前片段计划不一致",
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    try:
        current_content = await build_script_snapshot_content(session, segment)
    except AppError as exc:
        return {
            "status": "input_stale",
            "adoptable": False,
            "reason": exc.message,
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    current_fingerprint = fingerprint(current_content)
    matches = stored.get("script_fingerprint") == current_fingerprint
    if not matches and not reused:
        frozen = await session.get(SegmentScriptSnapshot, stored.get("script_snapshot_id"))
        matches = bool(frozen is not None and frozen.segment_id == segment.id
                       and frozen.fingerprint == stored.get("script_fingerprint")
                       and fingerprint(frozen.content) == frozen.fingerprint
                       and generation_content(frozen.content) == generation_content(current_content))
    if not matches and not reused:
        return {
            "status": "input_stale",
            "adoptable": False,
            "reason": "片段脚本或资产版本已变化，请重新生成候选",
            "media_status": media_status,
            "input_fingerprint": input_fingerprint,
            "script_fingerprint": stored.get("script_fingerprint"),
            "provider_model_id": input_snapshot.get("provider_model_id"),
        }
    dependency = input_snapshot.get("continuity_dependency")
    if isinstance(dependency, dict):
        source_lineage_key = dependency.get("source_segment_lineage_key")
        expected_version_id = dependency.get("source_video_version_id")
        current_version_id = await session.scalar(
            select(SegmentVideoVersion.id)
            .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
            .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
            .where(
                VideoSegment.episode_id == segment.episode_id,
                VideoSegment.lineage_key == source_lineage_key,
                VideoSegment.plan_id == plan.id,
                SegmentVideoVersion.is_final.is_(True),
            )
            .order_by(EpisodeProductionPlan.version.desc(), SegmentVideoVersion.version.desc())
        )
        if current_version_id != expected_version_id:
            return {
                "status": "input_stale",
                "adoptable": False,
                "reason": "前序片段采用版本已变化，请重新生成连续候选",
                "media_status": media_status,
                "input_fingerprint": input_fingerprint,
                "script_fingerprint": stored.get("script_fingerprint"),
                "provider_model_id": input_snapshot.get("provider_model_id"),
            }
    return {
        "status": "adopted" if version.is_final else "ready",
        "adoptable": not version.is_final,
        "reason": None,
        "media_status": media_status,
        "input_fingerprint": input_fingerprint,
        "script_fingerprint": stored.get("script_fingerprint"),
        "provider_model_id": input_snapshot.get("provider_model_id"),
    }
