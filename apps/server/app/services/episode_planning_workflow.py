"""Durable content analysis and frozen-detail jobs on the existing worker queue.

The public content-planning API uses this queue directly. This service has no
legacy-payload reader or provider-call fallback.
"""

import hashlib
import json
from copy import deepcopy

from pydantic import ValidationError as SchemaError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.models import Asset, Episode, Job, Project, ProjectAssetLink, utcnow
from app.models.episode_planning import EpisodePlanningRecord
from app.schemas.episode_planning import SegmentDetail, VideoCapability
from app.schemas.episode_references import SourceReferenceBinding
from app.schemas.episode_timing import ContentAnalysis
from app.services.episode_content_timing import prepare_sources
from app.services.episode_planning_assembly import assemble
from app.services.episode_planning_capability import load_capability
from app.services.episode_planning_contract import (
    fingerprint,
    parse_model_response,
    validate_detail,
)
from app.services.episode_planning_coordinator import create_plan, detail_prompt
from app.services.episode_planning_references import (
    bind_analysis,
    bound_analysis_prompt,
    resolve_bindings,
)
from app.services.episode_planning_storage import new_plan_record, read_content_analysis, read_plan
from app.services.episode_preparation_gate import require_preparation
from app.services.job_creation_service import create_text_job
from app.services.screenplay_preflight import project_catalog
from app.services.text_model_policy_service import inherit_job_snapshot
from app.services.video_prompt_compiler import COMPILER_VERSION

TARGET_RUN = "episode_content_planning"
TARGET_ANALYSIS = "episode_content_analysis"
TARGET_DETAIL = "episode_content_detail"
CHILD_TARGETS = {TARGET_ANALYSIS, TARGET_DETAIL}
TARGETS = CHILD_TARGETS | {TARGET_RUN}
PARALLEL_DETAILS = 3
MAX_DETAILS = 64
TERMINAL = {"succeeded", "failed", "cancelled"}


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _schema_failure(label: str, error: SchemaError) -> ValidationError:
    errors = [
        {"loc": item["loc"], "type": item["type"]}
        for item in error.errors(include_input=False, include_context=False)[:12]
    ]
    return ValidationError(f"{label}结构未通过校验，原始响应已保存", details={"errors": errors})


async def _source_context(session: AsyncSession, episode: Episode):
    project = await session.get(Project, episode.project_id)
    if project is None:
        raise ConflictError("项目不存在")
    rows = (
        await session.execute(
            select(Asset, ProjectAssetLink)
            .join(
                ProjectAssetLink,
                ProjectAssetLink.asset_id == Asset.id,
            )
            .where(
                ProjectAssetLink.project_id == project.id,
                ProjectAssetLink.production_archived.is_(False),
            )
            .order_by(Asset.id)
        )
    ).all()
    catalog = []
    assets = []
    for asset, link in rows:
        data = link.production_data or {}
        catalog.append(
            {
                "asset_id": asset.id,
                "asset_type": asset.asset_type,
                "asset_name": asset.name,
                "aliases": (data.get("profile") or {}).get("aliases") or [],
            }
        )
        assets.append(
            {
                "id": asset.id,
                "name": asset.name,
                "type": asset.asset_type,
                "updated_at": asset.updated_at.isoformat(),
                "data": data,
            }
        )
    known = {item["asset_name"] for item in catalog if item["asset_type"] == "character"}
    catalog.extend(
        item
        for item in await project_catalog(session, project.id)
        if item["asset_name"] not in known
    )
    sources = prepare_sources(episode.script or "", catalog)
    return sources, _digest(
        {"catalog": catalog, "assets": assets, "settings": project.creation_settings}
    )


async def create_run(
    session: AsyncSession,
    episode: Episode,
    *,
    planner_model_id: int,
    video_model_id: int,
    mode_key: str,
    request_id: str,
    expected_script_revision: int,
    background_music: bool,
    reference_bindings: tuple[SourceReferenceBinding, ...] = (),
    expected_input_fingerprint: str | None = None,
) -> Job:
    if not settings.episode_planning_epoch.strip():
        raise ConflictError("新规划执行命名空间尚未初始化")
    if not request_id.strip() or len(request_id) > 128 or type(background_music) is not bool:
        raise ValidationError("规划请求编号或配乐设置无效")
    await session.execute(
        update(Episode).where(Episode.id == episode.id).values(number=Episode.number)
    )
    await session.refresh(episode)
    await require_preparation(session, episode)
    if (
        episode.script_revision != expected_script_revision
        or episode.finalized_script_revision != episode.script_revision
        or episode.status == "archived"
    ):
        raise ConflictError("请先确认当前正文版本")
    spec = {
        "planner_model_id": planner_model_id,
        "video_model_id": video_model_id,
        "mode_key": mode_key,
        "script_revision": expected_script_revision,
        "background_music": background_music,
        "epoch": settings.episode_planning_epoch,
        "reference_bindings": [binding.model_dump(mode="json") for binding in reference_bindings],
        "expected_input_fingerprint": expected_input_fingerprint,
    }
    previous = (
        await session.scalars(
            select(Job)
            .where(
                Job.target_type == TARGET_RUN,
                Job.target_id == episode.id,
                Job.project_id == episode.project_id,
                Job.deleted_at.is_(None),
            )
            .order_by(Job.id.desc())
        )
    ).all()
    for item in previous:
        if item.payload.get("request_id") == request_id:
            if item.payload.get("request_spec") != spec:
                raise ConflictError("该请求编号已经用于其他规划参数")
            return item
    if any(item.status not in TERMINAL for item in previous):
        raise ConflictError("本集已有规划任务，请等待或在当前页面恢复")
    capability = await load_capability(session, video_model_id)
    mode = next((m for m in capability.modes if m.key == mode_key), None)
    if mode is None:
        raise ConflictError("请选择已配置的视频生成模式")
    if background_music and mode.bgm_control == "unsupported":
        raise ConflictError("该模式不支持所选配乐设置")
    sources, assets = await _source_context(session, episode)
    references = await resolve_bindings(session, episode, sources, mode, reference_bindings)
    assets = _digest({"assets": assets, "references": references})
    if expected_input_fingerprint is not None:
        from app.services.episode_planning_inputs import input_fingerprint

        current = input_fingerprint(
            sources, assets, capability, mode_key, background_music, episode.script_revision
        )
        if current != expected_input_fingerprint:
            raise ConflictError("正文、参考素材或模型能力已变化，请重新校验后确认费用")
    parent = Job(
        owner_id=episode.owner_id,
        project_id=episode.project_id,
        workspace_id=episode.workspace_id,
        job_type="text_batch",
        target_type=TARGET_RUN,
        target_id=episode.id,
        status="processing",
        progress=0,
        max_attempts=1,
        started_at=utcnow(),
        payload={
            "request_id": request_id,
            "request_spec": spec,
            "epoch": settings.episode_planning_epoch,
            "sources": sources.model_dump(mode="json"),
            "asset_fingerprint": assets,
            "reference_snapshot": references,
            "capability": capability.model_dump(mode="json"),
            "capability_fingerprint": fingerprint(capability),
            "editorial_target_ms": episode.duration_estimate * 1000
            if episode.duration_estimate
            else None,
            "expected_total": 1,
            "allow_dispatch": True,
        },
        result={"stage": "analyzing_content", "completed": 0, "total": 1},
    )
    session.add(parent)
    await session.flush()
    child = await _new_child(
        session, parent, TARGET_ANALYSIS, bound_analysis_prompt(sources, reference_bindings, references)
    )
    parent.provider_id, parent.provider, parent.model = (
        child.provider_id,
        child.provider,
        child.model,
    )
    parent.execution_policy_snapshot = deepcopy(child.execution_policy_snapshot)
    await session.flush()
    return parent


async def _new_child(session, parent, target, prompt, *, segment_key=None):
    from app.services.director_output_transport import freeze_job

    child = await create_text_job(
        session,
        parent.owner_id,
        provider_model_id=parent.payload["request_spec"]["planner_model_id"],
        prompt=prompt,
        project_id=parent.project_id,
        parameters={},
    )
    child.target_type, child.target_id, child.parent_job_id = target, parent.target_id, parent.id
    child.max_attempts = 1
    child.payload = {
        **child.payload,
        "planning_run_id": parent.id,
        "planning_epoch": parent.payload["epoch"],
        "segment_key": segment_key,
        "response_protocol": {
            "version": "episode-timing.v1" if target == TARGET_ANALYSIS else "episode-planning.v1",
            "schema": (ContentAnalysis if target == TARGET_ANALYSIS else SegmentDetail).model_json_schema(),
        },
    }
    if parent.execution_policy_snapshot:
        inherit_job_snapshot(parent, child)
    freeze_job(child)
    await session.flush()
    return child


async def _lock_parent(session, parent_id):
    # SessionLocal disables autoflush. Preserve this transaction's frozen plan
    # pointer before reloading the locked parent during child aggregation.
    await session.flush()
    await session.execute(update(Job).where(Job.id == parent_id).values(progress=Job.progress))
    parent = await session.get(Job, parent_id, populate_existing=True)
    if parent is None or parent.target_type != TARGET_RUN:
        raise ConflictError("规划任务不存在")
    return parent


async def validate_context(session, parent, *, check_current_capability=True, require_latest=True):
    if parent.deleted_at is not None or parent.status == "cancelled":
        raise ConflictError("规划任务已取消或删除")
    if parent.payload["epoch"] != settings.episode_planning_epoch:
        raise ConflictError("规划执行命名空间已变化，禁止旧任务继续运行")
    # Serialize final writes with a new run or an edit of this episode.
    await session.execute(
        update(Episode).where(Episode.id == parent.target_id).values(number=Episode.number)
    )
    newer = await session.scalar(
        select(Job.id)
        .where(
            Job.target_type == TARGET_RUN,
            Job.target_id == parent.target_id,
            Job.project_id == parent.project_id,
            Job.id > parent.id,
        )
        .limit(1)
    )
    if require_latest and newer is not None:
        raise ConflictError("已有后续规划，旧结果不会覆盖当前内容")
    episode = await session.get(Episode, parent.target_id, populate_existing=True)
    spec = parent.payload["request_spec"]
    if (
        episode is None
        or episode.project_id != parent.project_id
        or episode.owner_id != parent.owner_id
        or episode.script_revision != spec["script_revision"]
        or episode.finalized_script_revision != episode.script_revision
        or episode.status == "archived"
    ):
        raise ConflictError("正文或确认版本已变化，已保存结果不会覆盖新内容")
    await require_preparation(session, episode)
    sources, assets = await _source_context(session, episode)
    # A local switch may replace a retired channel, but still validates the
    # frozen source/media and the newly selected channel separately.
    capability = (
        await load_capability(session, spec["video_model_id"])
        if check_current_capability
        else VideoCapability.model_validate_json(json.dumps(parent.payload["capability"]))
    )
    if fingerprint(capability) != parent.payload["capability_fingerprint"]:
        raise ConflictError("模型规格已变化，请先检查新规格与当前规划")
    mode = next(m for m in capability.modes if m.key == spec["mode_key"])
    bindings = tuple(
        SourceReferenceBinding.model_validate_json(json.dumps(item))
        for item in spec["reference_bindings"]
    )
    references = await resolve_bindings(session, episode, sources, mode, bindings)
    assets = _digest({"assets": assets, "references": references})
    if (
        sources.model_dump(mode="json") != parent.payload["sources"]
        or assets != parent.payload["asset_fingerprint"]
        or references != parent.payload["reference_snapshot"]
    ):
        raise ConflictError("正文或资产参考版本已变化，请核对后重新规划")
    return episode, sources, capability


async def validate_execution(session, job):
    parent = await _lock_parent(session, job.parent_job_id)
    if job.payload.get("planning_epoch") != parent.payload["epoch"]:
        raise ConflictError("任务执行命名空间不一致")
    # Already queued calls were authorized by the original submission. Local
    # recovery prevents expansion of that queue, not execution of its siblings.
    await require_current_child(session, parent, job)
    await validate_context(session, parent)


async def finalize_result(session: AsyncSession, job: Job, result: dict) -> dict:
    parent = await _lock_parent(session, job.parent_job_id)
    await require_current_child(session, parent, job)
    episode, sources, capability = await validate_context(session, parent)
    if str(job.worker_id or "").startswith("local-recovery-"):
        parent.payload = {**parent.payload, "allow_dispatch": False}
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("模型没有返回内容，原始响应已保留")
    if job.target_type == TARGET_ANALYSIS:
        try:
            analysis = parse_model_response(text, ContentAnalysis, "内容分析")
            bindings = tuple(
                SourceReferenceBinding.model_validate_json(json.dumps(item))
                for item in parent.payload["request_spec"]["reference_bindings"]
            )
            analysis = bind_analysis(analysis, bindings)
            from app.services.episode_audio_timing import calibrate_analysis
            analysis = calibrate_analysis(sources, analysis, parent.payload["reference_snapshot"])
            plan_id = parent.payload.get("planning_record_id")
            if plan_id is None:
                revision = (
                    await session.scalar(
                        select(func.max(EpisodePlanningRecord.revision)).where(
                            EpisodePlanningRecord.episode_id == episode.id,
                            EpisodePlanningRecord.execution_epoch == parent.payload["epoch"],
                        )
                    )
                    or 0
                ) + 1
                planned = create_plan(
                    sources=sources,
                    analysis=analysis,
                    capability=capability,
                    mode_key=parent.payload["request_spec"]["mode_key"],
                    episode_id=episode.id,
                    script_revision=episode.script_revision,
                    asset_fingerprint=parent.payload["asset_fingerprint"],
                    execution_epoch=parent.payload["epoch"],
                    plan_revision=revision,
                    compiler_version=COMPILER_VERSION,
                    editorial_target_ms=parent.payload["editorial_target_ms"],
                    background_music=parent.payload["request_spec"]["background_music"],
                )
                if len(planned.plan.segments) > MAX_DETAILS:
                    raise ValueError("超过本次详细脚本调用数量上限，请核对内容边界")
                record = new_plan_record(
                    planned.plan, workspace_id=episode.workspace_id, analysis=analysis
                )
                session.add(record)
                await session.flush()
                parent.payload = {
                    **parent.payload,
                    "planning_record_id": record.id,
                    "expected_total": 1 + len(planned.plan.segments),
                }
            else:
                record = await session.get(EpisodePlanningRecord, plan_id)
                if record is None or read_content_analysis(record) != analysis:
                    raise ConflictError("已冻结计划与当前响应不一致")
        except SchemaError as exc:
            raise _schema_failure("内容分析", exc) from exc
        except ValueError as exc:
            raise ValidationError(f"内容规划未通过校验：{str(exc)[:500]}") from exc
        return {
            "planning_record_id": record.id,
            "stage": "content_frozen",
            "usage": result.get("usage") or {},
        }
    record = await session.get(EpisodePlanningRecord, parent.payload.get("planning_record_id"))
    if record is None:
        raise ConflictError("详细脚本缺少冻结计划")
    try:
        plan = read_plan(record)
        detail = parse_model_response(text, SegmentDetail, "片段详细脚本")
        if detail.segment_key != job.payload.get("segment_key"):
            raise ValueError("Response belongs to another segment")
        validate_detail(plan, detail)
    except SchemaError as exc:
        raise _schema_failure("片段详细脚本", exc) from exc
    except ValueError as exc:
        raise ValidationError(f"片段详细脚本未通过校验：{str(exc)[:500]}") from exc
    return {
        "planning_record_id": record.id,
        "detail": detail.model_dump(mode="json"),
        "stage": "detail_saved",
        "usage": result.get("usage") or {},
    }


async def current_children(session, parent):
    children = (
        await session.scalars(select(Job).where(Job.parent_job_id == parent.id).order_by(Job.id))
    ).all()
    latest = {}
    for child in children:
        if child.target_type not in CHILD_TARGETS:
            raise ConflictError("规划任务包含未知子任务")
        latest[(child.target_type, child.payload.get("segment_key"))] = child
    return list(latest.values())


async def require_current_child(session, parent, job):
    if job.id not in {child.id for child in await current_children(session, parent)}:
        raise ConflictError("该片段任务已被替代，不会写入旧结果")


async def aggregate_run(session: AsyncSession, parent: Job) -> Job:
    parent = await _lock_parent(session, parent.id)
    if parent.status == "cancelled" or parent.deleted_at is not None:
        return parent
    children = await current_children(session, parent)
    failed = [job for job in children if job.status in {"failed", "cancelled"}]
    active = [job for job in children if job.status not in TERMINAL]
    completed = sum(job.status == "succeeded" for job in children)
    expected = parent.payload["expected_total"]
    stage = "analyzing_content"
    record_id = parent.payload.get("planning_record_id")
    if record_id:
        record = await session.get(EpisodePlanningRecord, record_id)
        if record is None:
            raise ConflictError("冻结计划不存在")
        plan = read_plan(record)
        present = {
            job.payload.get("segment_key") for job in children if job.target_type == TARGET_DETAIL
        }
        pending = [segment for segment in plan.segments if segment.key not in present]
        stage = "writing_details"
        if not failed and parent.payload.get("allow_dispatch"):
            await validate_context(session, parent)
            analysis = read_content_analysis(record)
            for segment in pending[: max(0, PARALLEL_DETAILS - len(active))]:
                child = await _new_child(
                    session,
                    parent,
                    TARGET_DETAIL,
                    detail_prompt(plan, segment.key, analysis),
                    segment_key=segment.key,
                )
                active.append(child)
        if completed == expected:
            stage = "details_ready"
        elif not active and not parent.payload.get("allow_dispatch") and not failed:
            stage = "awaiting_paid_continuation"
    parent.progress = min(99, int(completed / expected * 100))
    parent.result = {
        "stage": stage,
        "completed": completed,
        "total": expected,
        "planning_record_id": record_id,
        "failed_job_ids": [job.id for job in failed],
    }
    if completed == expected and record_id:
        await validate_context(session, parent)
        details = tuple(
            SegmentDetail.model_validate_json(json.dumps(child.result["detail"]))
            for child in children
            if child.target_type == TARGET_DETAIL
        )
        assembled = assemble(read_plan(record), read_content_analysis(record), details)
        parent.result = {
            **parent.result,
            "assembly": assembled,
            "detail_job_ids": [
                child.id for child in children if child.target_type == TARGET_DETAIL
            ],
        }
        parent.status, parent.progress, parent.finished_at = "succeeded", 100, utcnow()
        parent.error_code = parent.error_message = None
    elif not active and (failed or stage == "awaiting_paid_continuation"):
        parent.status, parent.finished_at = "failed", utcnow()
        parent.error_code = (
            "PLANNING_PARTIAL_FAILURE" if failed else "PLANNING_CONTINUATION_REQUIRED"
        )
        parent.error_message = "已保留完成结果；先恢复已有响应，新增模型调用需要确认费用与范围"
    else:
        parent.status, parent.finished_at = "processing", None
        parent.error_code = parent.error_message = None
    await session.flush()
    return parent
