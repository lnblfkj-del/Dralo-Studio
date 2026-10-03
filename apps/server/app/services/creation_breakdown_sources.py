"""M3 剧本研读与资产拆解业务逻辑：分批研读、资产候选生成与确认入库。

本模块处理长剧本的分批 AI 研读和结构化资产拆解的完整流程。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_TYPE_STORY_BIBLE,
    ASSET_TYPE_CHARACTER,
    ASSET_TYPE_COSTUME,
    ASSET_TYPE_PROP,
    ASSET_TYPE_SCENE,
    ASSET_TYPE_VOICE,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    CreationArtifact,
    CreationSession,
    Job,
)
from app.services import (
    script_finalization_service,
)
from app.services.creation_asset_scope import (
    build_narrative_asset_scope,
    narrative_asset_scope_changed,
)
from app.services.creation_session_service import (
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
)
from app.services.narrative_spec_service import narrative_spec_from_settings
from app.services.script_source_snapshot import script_snapshot_is_current

R4_REQUIREMENT_GROUPS = (
    ("character", ASSET_TYPE_CHARACTER, "characters"),
    ("costume", ASSET_TYPE_COSTUME, "costumes"),
    ("scene", ASSET_TYPE_SCENE, "scenes"),
    ("prop", ASSET_TYPE_PROP, "props"),
    ("character_voice", ASSET_TYPE_VOICE, "character_voices"),
    ("music", ASSET_TYPE_VOICE, "music"),
    ("ambience", ASSET_TYPE_VOICE, "ambience"),
    ("sound_effect", ASSET_TYPE_VOICE, "sound_effects"),
)
R4_REQUIREMENT_TYPES = tuple(entry[0] for entry in R4_REQUIREMENT_GROUPS)
BREAKDOWN_ACTIVE_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
}


def _artifact_source(artifact: CreationArtifact | None) -> dict[str, int]:
    if artifact is None:
        return {}
    return {
        "artifact_id": artifact.id,
        "version": artifact.version,
        "revision": artifact.revision,
    }


async def _validate_breakdown_story_source(
    session: AsyncSession,
    item: CreationSession,
    source: dict[str, Any],
) -> None:
    current = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
            CreationArtifact.status == ARTIFACT_STATUS_CONFIRMED,
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    latest_active = await session.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
            CreationArtifact.status.in_(
                {
                    ARTIFACT_STATUS_CONFIRMED,
                    ARTIFACT_STATUS_DRAFT,
                }
            ),
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    if latest_active is not None and latest_active.status == ARTIFACT_STATUS_DRAFT:
        raise ConflictError("故事设定存在待确认新版本，请先完成来源审核再重新提取资产")
    if not source:
        return
    expected = (
        int(source.get("artifact_id") or 0),
        int(source.get("version") or 0),
        int(source.get("revision") or 0),
    )
    actual = (
        current.id if current is not None else 0,
        current.version if current is not None else 0,
        current.revision if current is not None else 0,
    )
    if actual != expected:
        raise ConflictError("故事设定已更新，本次旧资产拆解不能继续，请重新提取")
    if latest_active is not None and current is not None and latest_active.id != current.id:
        raise ConflictError("故事设定存在待确认新版本，请先完成来源审核再重新提取资产")


async def validate_script_asset_breakdown_job_sources(
    session: AsyncSession,
    job: Job,
    *,
    require_parent_active: bool,
) -> CreationSession:
    supported = {
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
        JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
    }
    if job.target_type not in supported or job.target_id is None:
        raise ConflictError("资产拆解任务来源无效")
    item = await session.get(CreationSession, job.target_id)
    if item is None or item.owner_id != job.owner_id or item.project_id is None:
        raise ConflictError("创作会话不可用")
    parent = None
    if job.parent_job_id is not None:
        parent = await session.get(Job, job.parent_job_id)
        if parent is None or parent.target_type != JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP:
            raise ConflictError("资产拆解批次不存在")
        if require_parent_active and parent.status not in BREAKDOWN_ACTIVE_STATUSES:
            raise ConflictError("资产拆解批次已结束或取消，旧结果不能继续写入")
    source_job = parent or job
    payload = dict(source_job.payload or {})
    parameters = dict((job.payload or {}).get("parameters") or {})
    production_context = dict(parameters.get("production_context") or {})
    frozen_narrative_scope = dict(production_context.get("narrative_scope") or {})
    frozen_revision = frozen_narrative_scope.get("narrative_spec_revision")
    if frozen_revision is not None:
        current_spec = narrative_spec_from_settings(item.settings)
        current_scope = build_narrative_asset_scope(
            item.settings,
            [dict(row) for row in production_context.get("episodes") or []],
        )
        revision_changed = int(current_spec.get("revision") or 0) != int(frozen_revision)
        if revision_changed and narrative_asset_scope_changed(
            frozen_narrative_scope,
            current_scope,
        ):
            raise ConflictError("剧集结构规格已更新，本次旧资产拆解不能继续，请重新提取")
    fingerprint = str(
        payload.get("input_fingerprint")
        or parameters.get("input_fingerprint")
        or production_context.get("input_fingerprint")
        or ""
    )
    source_revisions = dict(
        payload.get("source_script_revisions")
        or parameters.get("source_script_revisions")
        or production_context.get("source_script_revisions")
        or {}
    )
    current = (
        await script_snapshot_is_current(session, item.project_id, fingerprint)
        if fingerprint
        else await script_finalization_service.revisions_are_current(
            session, item.project_id, source_revisions
        )
    )
    if not current:
        raise ConflictError("正式剧本版本已变化，本次旧资产拆解不能继续，请重新提取")
    story_source = dict(
        payload.get("story_source")
        or parameters.get("story_source")
        or production_context.get("story_source")
        or {}
    )
    await _validate_breakdown_story_source(session, item, story_source)
    return item
