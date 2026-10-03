"""Explicit paid recovery for failed script asset-breakdown ranges."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import CreationSession, Job, JobTextResponse, utcnow
from app.services import project_service
from app.services.creation_breakdown_jobs import _create_asset_breakdown_child
from app.services.creation_breakdown_planning import (
    VISUAL_REQUIREMENT_TYPES,
    breakdown_scope_key,
    build_breakdown_work_units,
    restore_work_unit,
    smaller_work_units,
    visual_asset_key_context,
)
from app.services.creation_breakdown_sources import (
    validate_script_asset_breakdown_job_sources,
)
from app.services.creation_session_service import append_message

ASSET_BATCH = "script_asset_breakdown_batch"
ASSET_GROUP = "script_asset_breakdown_group"
TERMINAL_FAILURES = {"failed", "cancelled"}


def _original_error_code(job: Job) -> str:
    history = list((job.payload or {}).get("attempt_history") or [])
    if history:
        return str(history[-1].get("error_code") or job.error_code or "")
    return str(job.error_code or "")


async def _latest_response(session: AsyncSession, job_id: int) -> JobTextResponse | None:
    return await session.scalar(
        select(JobTextResponse)
        .where(JobTextResponse.job_id == job_id)
        .order_by(JobTextResponse.received_at.desc(), JobTextResponse.id.desc())
        .limit(1)
    )


async def _failed_children(session: AsyncSession, job: Job) -> tuple[Job, list[Job]]:
    if job.target_type == ASSET_BATCH:
        if job.parent_job_id is None:
            raise ConflictError("资产拆解子任务缺少父任务")
        parent = await session.get(Job, job.parent_job_id)
        children = [job]
    elif job.target_type == ASSET_GROUP:
        parent = job
        children = list((await session.scalars(
            select(Job).where(
                Job.parent_job_id == job.id,
                Job.status.in_(TERMINAL_FAILURES),
            )
        )).all())
        children = [
            child for child in children
            if (child.resolution or {}).get("status") not in {
                "superseded",
                "replacement_pending",
            }
        ]
    else:
        raise ConflictError("当前仅支持恢复资产拆解失败范围")
    if parent is None or parent.target_type != ASSET_GROUP:
        raise ConflictError("资产拆解父任务不存在")
    if not children:
        raise ConflictError("没有需要恢复的资产拆解范围")
    return parent, children


async def _assert_paid_recall_allowed(
    session: AsyncSession,
    child: Job,
    *,
    channel_checked: bool,
) -> None:
    submission = dict((child.payload or {}).get("text_submission") or {})
    if submission.get("response_received"):
        stored = await _latest_response(session, child.id)
        if (stored is not None and stored.status == "available"
                and stored.processing_attempts < 1 and not child.asset_response_truncated):
            raise ConflictError(
                f"子任务 #{child.id} 已保存模型响应，请先执行一次无费用的本地重新处理"
            )
    elif submission.get("status") == "submitted":
        if not channel_checked:
            raise ConflictError(
                f"子任务 #{child.id} 的远端结果不明确，请先核对渠道后台并确认未生成"
            )
    else:
        raise ConflictError(
            f"子任务 #{child.id} 可使用普通失败范围重试，无需使用付费确认恢复"
        )


def _replace_scope(order: list[str], old: str, replacements: list[str]) -> list[str]:
    if old not in order:
        return [*order, *[key for key in replacements if key not in order]]
    result: list[str] = []
    for key in order:
        if key == old:
            result.extend(replacements)
        else:
            result.append(key)
    return list(dict.fromkeys(result))


async def confirm_asset_breakdown_recall(
    session: AsyncSession,
    job: Job,
    *,
    actor_id: int,
    channel_checked: bool,
    reason: str,
) -> Job:
    """Create replacement jobs only for explicitly confirmed failed ranges."""
    parent, failed_children = await _failed_children(session, job)
    item = await validate_script_asset_breakdown_job_sources(
        session, parent, require_parent_active=False
    )
    from app.services.team_access import same_team
    if not await same_team(session, item.owner_id, actor_id):
        raise ConflictError("无权恢复该资产拆解任务")
    for child in failed_children:
        await validate_script_asset_breakdown_job_sources(
            session, child, require_parent_active=False
        )
        await _assert_paid_recall_allowed(
            session, child, channel_checked=channel_checked
        )

    payload = dict(parent.payload or {})
    progress = dict((item.settings or {}).get("asset_breakdown") or {})
    episodes = await project_service.list_episodes(session, item.project_id or 0)
    by_number = {episode.number: episode for episode in episodes}
    execution = dict(payload.get("agent_execution") or {})
    scope_order = list(payload.get("scope_order") or progress.get("scope_order") or [])
    visual_scope_keys = list(
        payload.get("visual_scope_keys") or progress.get("visual_scope_keys") or []
    )
    batches = dict(progress.get("batches") or {})
    visual_breakdowns = [batches[key] for key in visual_scope_keys if key in batches]
    replacements: list[Job] = []
    recall_history = list(payload.get("recall_history") or [])

    for child in failed_children:
        parameters = dict((child.payload or {}).get("parameters") or {})
        numbers = [int(value) for value in parameters.get("episode_numbers") or []]
        child_episodes = tuple(by_number[number] for number in numbers if number in by_number)
        if len(child_episodes) != len(numbers):
            raise ConflictError(f"子任务 #{child.id} 的分集范围已经失效")
        requirement_types = list(parameters.get("requirement_types") or [])
        group = str(parameters.get("requirement_group") or "visual")
        if parameters.get("source_ranges"):
            units = [restore_work_unit(child_episodes, requirement_types, group, parameters["source_ranges"])]
        else:
            visual_units, audio_units = build_breakdown_work_units(child_episodes, requirement_types)
            units = visual_units if group == "visual" else audio_units
        if _original_error_code(child) == "MODEL_OUTPUT_TRUNCATED":
            original = restore_work_unit(child_episodes, requirement_types, group, parameters.get("source_ranges"))
            # A new long-script plan may already divide an old, unsplit job.
            if len(units) == 1 or len(numbers) > 1:
                units = smaller_work_units(original)
        if not units:
            raise ConflictError(f"子任务 #{child.id} 的恢复范围为空")

        old_scope_key = str(parameters.get("scope_key") or "")
        new_scope_keys = [
            breakdown_scope_key(
                input_fingerprint=str(payload.get("input_fingerprint") or ""),
                group=unit.group,
                requirement_types=unit.requirement_types,
                episode_numbers=unit.episode_numbers,
                provider_model_id=int(payload["provider_model_id"]),
                skill_key=execution.get("skill_key"),
                skill_version=execution.get("skill_version"),
                source_ranges=unit.source_ranges,
            )
            for unit in units
        ]
        scope_order = _replace_scope(scope_order, old_scope_key, new_scope_keys)
        if parameters.get("requirement_group") == "visual":
            visual_scope_keys = _replace_scope(
                visual_scope_keys, old_scope_key, new_scope_keys
            )
        created_for_child: list[Job] = []
        for unit, scope_key in zip(units, new_scope_keys, strict=True):
            context: dict[str, Any] = {
                "production_context": dict(progress.get("production_context") or {}),
                "existing_assets": list(payload.get("existing_assets") or []),
            }
            if unit.group == "audio":
                context["visual_asset_keys"] = visual_asset_key_context(
                    visual_breakdowns, episode_numbers=unit.episode_numbers
                )
            replacement = await _create_asset_breakdown_child(
                session,
                item,
                parent,
                model_id=int(payload["provider_model_id"]),
                execution=execution,
                instruction_prefix=(
                    str(payload.get("instruction_prefix") or "")
                    + "\n这是用户确认后的失败范围定向重建。只返回当前范围，严格遵守 JSON 结构。"
                ),
                unit=unit,
                batch_index=len(replacements) + len(scope_order),
                total_batches=len(scope_order),
                context=context,
                protocol=str(payload.get("protocol") or "openai_compatible"),
                capabilities=list(payload.get("model_capabilities") or []),
                source_revisions={
                    str(episode.id): episode.script_revision for episode in unit.episodes
                },
                input_fingerprint=str(payload.get("input_fingerprint") or ""),
                story_source=dict(payload.get("story_source") or {}),
                requested_numbers=list(payload.get("requested_episode_numbers") or []),
                scope_key=scope_key,
            )
            replacement.payload = {
                **replacement.payload,
                "recovery": {
                    "kind": "confirmed_paid_recall",
                    "replaces_job_id": child.id,
                    "confirmed_by": actor_id,
                    "confirmed_at": utcnow().isoformat(),
                    "reason": reason,
                },
            }
            created_for_child.append(replacement)
            replacements.append(replacement)
        child.payload = {
            **(child.payload or {}),
            "resolution": {
                "status": "replacement_pending",
                "by_job_ids": [replacement.id for replacement in created_for_child],
                "confirmed_by": actor_id,
                "confirmed_at": utcnow().isoformat(),
            },
        }
        recall_history.append({
            "source_job_id": child.id,
            "replacement_job_ids": [item.id for item in created_for_child],
            "old_scope_key": old_scope_key,
            "new_scope_keys": new_scope_keys,
            "reason": reason,
            "confirmed_by": actor_id,
            "confirmed_at": utcnow().isoformat(),
        })

    expected_total = len(scope_order)
    for replacement in replacements:
        replacement_parameters = dict((replacement.payload or {}).get("parameters") or {})
        replacement.payload = {
            **(replacement.payload or {}),
            "parameters": {
                **replacement_parameters,
                "total_batches": expected_total,
            },
        }
    parent.payload = {
        **payload,
        "scope_order": scope_order,
        "visual_scope_keys": visual_scope_keys,
        "expected_total": expected_total,
        "total_batches": expected_total,
        "recall_history": recall_history[-20:],
        "recovery_summary": {
            "failed_scopes": 0,
            "local_reprocess_required": 0,
            "channel_check_required": 0,
        },
    }
    parent.result = {
        **(parent.result or {}),
        "total": expected_total,
        "child_job_ids": [
            *list((parent.result or {}).get("child_job_ids") or []),
            *[replacement.id for replacement in replacements],
        ],
    }
    parent.status = "processing"
    parent.finished_at = None
    parent.error_code = None
    parent.error_message = None
    failed_scopes = dict(progress.get("failed_scopes") or {})
    for child in failed_children:
        scope_key = str(dict((child.payload or {}).get("parameters") or {}).get("scope_key") or "")
        failed_scopes.pop(scope_key, None)
    progress.update({
        "status": "running",
        "completed": False,
        "scope_order": scope_order,
        "visual_scope_keys": visual_scope_keys,
        "total_batches": expected_total,
        "failed_scopes": failed_scopes,
        "parent_job_id": parent.id,
    })
    settings = dict(item.settings)
    settings["asset_breakdown"] = progress
    item.settings = settings
    await append_message(
        session,
        item.id,
        "user",
        "asset_breakdown_recall_confirmed",
        f"已确认重新调用 {len(replacements)} 个失败范围；已成功范围不会重复提交。",
        job_id=parent.id,
    )
    await session.flush()
    return parent
