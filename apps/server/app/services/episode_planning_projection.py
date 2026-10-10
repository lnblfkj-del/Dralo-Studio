"""Durable production drafts from verified frozen results, without activation.

Draft shots stay embedded with their source identities. They must not be inserted
into the episode's live Scene/Shot tables before the production switch is ready.
"""

from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.models import EpisodeProductionPlan, VideoSegment, utcnow
from app.models.episode_planning import EpisodePlanningRecord
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_assembly import read_assembly
from app.services.episode_planning_contract import fingerprint
from app.services.episode_planning_storage import read_content_analysis, read_plan

SOURCE_TYPE = "content_frozen"


def segment_values(plan, item, order):
    from app.services.episode_planning_video_preview import _semantics

    mode = next(mode for mode in plan.capability.modes if mode.key == item["mode_key"])
    # This is the readable script, not a certified provider prompt. Keep every
    # original source string even when background music is disabled.
    lines = []
    for shot in item["shots"]:
        direction = shot["direction"]
        lines.append(
            f"{direction['shot_size']} / {direction['camera_angle']} / {direction['camera_movement']}"
        )
        lines.extend(source["text"] for source in shot["sources"])
        lines.append(direction["action"])
    return {
        "episode_id": plan.episode_id,
        "order": order,
        "lineage_key": item["key"],
        "parent_lineage_keys": [],
        "title": f"片段 {order:02d}",
        "generation_duration": item["requested_duration_ms"] / 1000,
        "timeline_duration": item["used_duration_ms"] / 1000,
        "trim_in": item["safe_head_ms"] / 1000,
        "trim_out": item["safe_tail_ms"] / 1000,
        "prompt": "\n".join(lines),
        "negative_prompt": None,
        "status": "pending",
        "parameters": {
            "content_plan_fingerprint": fingerprint(plan),
            "mode_key": mode.key,
            "aspect_ratio": mode.aspect_ratio,
            "resolution": mode.resolution,
            "input_mode": mode.input_mode,
            "background_music": item["background_music"],
            "video_submission_ready": False,
            "structured_script": _semantics(item)[0],
        },
        "refs": {"content_segment": item},
    }


async def project_draft(session, parent, actor_id, *, expected_fingerprint):
    return await _projection(
        session, parent, actor_id, expected_fingerprint=expected_fingerprint, create=True
    )


async def read_projection(session, parent, *, expected_fingerprint):
    """Verify an existing projection without creating or updating production data."""
    return await _projection(
        session, parent, None, expected_fingerprint=expected_fingerprint, create=False
    )


async def _projection(session, parent, actor_id, *, expected_fingerprint, create):
    parent = await workflow._lock_parent(session, parent.id)
    # A new unactivated revision supersedes old writes, not the active video's
    # immutable source. Reading still checks epoch, script, assets and channel.
    episode, _, _ = await workflow.validate_context(session, parent, require_latest=create)
    if parent.status != "succeeded" or not (parent.result or {}).get("assembly"):
        raise ConflictError("片段详细脚本尚未全部完成，不能保存生产草稿")
    record = await session.get(EpisodePlanningRecord, parent.payload.get("planning_record_id"))
    if (
        record is None
        or record.episode_id != episode.id
        or record.workspace_id != episode.workspace_id
        or record.execution_epoch != parent.payload["epoch"]
    ):
        raise ConflictError("生产草稿的冻结来源不匹配")
    try:
        plan = read_plan(record)
        assembly = read_assembly(plan, read_content_analysis(record), parent.result["assembly"])
    except ValueError as exc:
        raise ConflictError("冻结片段结果完整性检查失败，请保留结果后核查") from exc
    if assembly["fingerprint"] != expected_fingerprint:
        raise ConflictError("片段脚本已变化，请刷新后保存")
    parameters = {
        "planning_record_id": record.id,
        "planning_run_id": parent.id,
        "plan_fingerprint": record.fingerprint,
        "assembly_fingerprint": assembly["fingerprint"],
        "execution_epoch": plan.execution_epoch,
        "timeline_ms": assembly["timeline_ms"],
        "video_submission_ready": False,
    }
    values = [
        segment_values(plan, item, order)
        for order, item in enumerate(assembly["segments"], start=1)
    ]
    projection = parent.payload.get("production_projection")
    if projection:
        stored = await session.get(EpisodeProductionPlan, projection["plan_id"])
        if (
            stored is None
            or stored.episode_id != episode.id
            or stored.owner_id != episode.owner_id
            or stored.workspace_id != episode.workspace_id
            or stored.source_type != SOURCE_TYPE
            or stored.status not in {"draft", "confirmed"}
            or stored.parameters != parameters
            or stored.source_script_revision != plan.script_revision
            or stored.provider_model_id != plan.capability.provider_model_id
            or stored.model_capability_snapshot != plan.capability.model_dump(mode="json")
            or stored.total_timeline_duration != assembly["timeline_ms"] / 1000
            or stored.total_generation_duration
            != sum(s.requested_duration_ms for s in plan.segments) / 1000
        ):
            raise ConflictError("生产草稿与冻结来源不一致，拒绝覆盖")
        segments = list(
            (
                await session.scalars(
                    select(VideoSegment)
                    .where(VideoSegment.plan_id == stored.id)
                    .order_by(VideoSegment.order)
                )
            ).all()
        )
        if len(segments) != len(values) or any(
            segment.workspace_id != episode.workspace_id
            or any(
                getattr(segment, key) != value
                for key, value in expected.items()
                if key != "status" or stored.status == "draft"
            )
            for segment, expected in zip(segments, values, strict=True)
        ):
            raise ConflictError("生产片段已被修改，不能假装保存成功或覆盖修改")
    else:
        if not create:
            raise ConflictError("冻结计划缺少已保存的生产草稿")
        version = (
            await session.scalar(
                select(func.max(EpisodeProductionPlan.version)).where(
                    EpisodeProductionPlan.episode_id == episode.id
                )
            )
            or 0
        ) + 1
        stored = EpisodeProductionPlan(
            episode_id=episode.id,
            owner_id=episode.owner_id,
            workspace_id=episode.workspace_id,
            version=version,
            source_type=SOURCE_TYPE,
            status="draft",
            source_script_revision=plan.script_revision,
            provider_model_id=plan.capability.provider_model_id,
            model_capability_snapshot=plan.capability.model_dump(mode="json"),
            parameters=parameters,
            total_timeline_duration=assembly["timeline_ms"] / 1000,
            total_generation_duration=sum(s.requested_duration_ms for s in plan.segments) / 1000,
        )
        session.add(stored)
        await session.flush()
        segments = [
            VideoSegment(plan_id=stored.id, workspace_id=episode.workspace_id, **value)
            for value in values
        ]
        session.add_all(segments)
        await session.flush()
        parent.payload = {
            **parent.payload,
            "production_projection": {
                "plan_id": stored.id,
                "assembly_fingerprint": expected_fingerprint,
                "actor_id": actor_id,
                "saved_at": utcnow().isoformat(),
            },
        }
        await session.flush()
    # No active_plan_id update, legacy Shot insertion, media queue, or compilation.
    return {
        "id": stored.id,
        "version": stored.version,
        "status": stored.status,
        "assembly_fingerprint": assembly["fingerprint"],
        "timeline_ms": assembly["timeline_ms"],
        "segments": [
            {"id": segment.id, **item}
            for segment, item in zip(segments, assembly["segments"], strict=True)
        ],
        "model_called": False,
        "video_submission_ready": False,
    }
