"""Atomically materialize frozen shots and activate their production timeline."""

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import (
    EpisodeProduction,
    EpisodeProductionPlan,
    Job,
    Scene,
    Shot,
    VideoSegment,
    VideoSegmentShot,
    utcnow,
)
from app.services import episode_planning_workflow as workflow
from app.services.episode_planning_projection import project_draft
from app.services.job_concurrency_service import TERMINAL_STATUSES
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED


def shot_values(item, scene, order, record_id):
    sources = item["sources"]
    return {
        "scene_id": scene.id,
        "owner_id": scene.owner_id,
        "workspace_id": scene.workspace_id,
        "order": order,
        "duration": (item["end_ms"] - item["start_ms"]) / 1000,
        **{
            key: item["direction"][key]
            for key in ("shot_size", "camera_angle", "camera_movement", "action")
        },
        "dialogue": "\n".join(s["text"] for s in sources if s["kind"] in {"dialogue", "narration"}),
        "audio_note": "\n".join(s["text"] for s in sources if s["kind"] in {"sound", "music"}),
        "refs": {"planning_record_id": record_id, "content_shot": item},
    }


async def verify_shots(session, production_plan, assembly):
    rows = (
        await session.execute(
            select(VideoSegmentShot, Shot, Scene, VideoSegment.lineage_key)
            .join(Shot, Shot.id == VideoSegmentShot.shot_id)
            .join(Scene, Scene.id == Shot.scene_id)
            .join(VideoSegment, VideoSegment.id == VideoSegmentShot.segment_id)
            .where(VideoSegment.plan_id == production_plan.id)
            .order_by(VideoSegment.order, VideoSegmentShot.order)
        )
    ).all()
    expected = [
        (segment, shot, index)
        for segment in assembly["segments"]
        for index, shot in enumerate(segment["shots"], 1)
    ]
    if len(rows) != len(expected):
        raise ConflictError("冻结镜头关联缺失，请重试保存或核查生产计划")
    scene_orders, scene_identities = {}, {}
    for (link, shot, scene, lineage_key), (segment, original, index) in zip(
        rows, expected, strict=True
    ):
        scene_key = original["sources"][0]["scene_key"]
        if scene_key not in scene_identities:
            name = next(
                (s["text"] for s in original["sources"] if s["kind"] == "scene"),
                scene_key or f"场景 {len(scene_identities) + 1}",
            )
            scene_identities[scene_key] = (scene.id, len(scene_identities) + 1, name[:255])
        scene_orders[scene.id] = scene_orders.get(scene.id, 0) + 1
        values = shot_values(
            original,
            scene,
            scene_orders[scene.id],
            production_plan.parameters["planning_record_id"],
        )
        if (
            lineage_key != segment["key"]
            or (scene.id, scene.order, scene.name) != scene_identities[scene_key]
            or scene.episode_id != production_plan.episode_id
            or scene.owner_id != production_plan.owner_id
            or scene.workspace_id != production_plan.workspace_id
            or shot.workspace_id != production_plan.workspace_id
            or any(getattr(shot, key) != value for key, value in values.items())
            or link.workspace_id != production_plan.workspace_id
            or link.order != index
            or link.start_time != (original["start_ms"] + segment["safe_head_ms"]) / 1000
            or link.end_time != (original["end_ms"] + segment["safe_head_ms"]) / 1000
        ):
            raise ConflictError("正式镜头或时间线与冻结计划不一致，请重新核对")
    return rows


async def activate(
    session,
    parent,
    actor_id,
    *,
    expected_fingerprint,
    expected_production_revision,
    expected_active_plan_id,
):
    draft = await project_draft(
        session, parent, actor_id, expected_fingerprint=expected_fingerprint
    )
    parent = await workflow._lock_parent(session, parent.id)
    episode, _, _ = await workflow.validate_context(session, parent)
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    plan = await session.get(EpisodeProductionPlan, draft["id"])
    assembly = parent.result["assembly"]
    baseline = {
        "expected_fingerprint": expected_fingerprint,
        "expected_production_revision": expected_production_revision,
        "expected_active_plan_id": expected_active_plan_id,
    }
    audit = parent.payload.get("production_activation")
    if audit:
        if (
            audit["request"] != baseline
            or production is None
            or production.active_plan_id != plan.id
            or plan.status != "confirmed"
        ):
            raise ConflictError("该启用请求的来源或当前计划已变化，请刷新后核对")
        await verify_shots(session, plan, assembly)
        return {
            **draft,
            "status": "confirmed",
            "production_revision": production.revision,
            "active_plan_id": plan.id,
            "model_called": False,
        }
    if (production.revision if production else 0) != expected_production_revision or (
        production.active_plan_id if production else None
    ) != expected_active_plan_id:
        raise ConflictError("当前制作计划已变化，请刷新后重新启用")
    if plan.status != "draft":
        raise ConflictError("仅可启用经过核对的冻结草稿")
    if any(s["overlap_previous_ms"] for s in assembly["segments"]):
        raise ConflictError("当前生产时间线尚未支持重叠片段，请重新核对规划")
    active_job = await session.scalar(
        select(Job.id)
        .where(
            Job.project_id == episode.project_id,
            Job.status.not_in(TERMINAL_STATUSES),
            (
                (Job.target_type == "video_segment")
                & Job.target_id.in_(
                    select(VideoSegment.id).where(VideoSegment.episode_id == episode.id)
                )
            )
            | (
                (
                    Job.target_type.in_(
                        ["episode_video_batch", "episode_export", "episode_edit_render"]
                    )
                )
                & (Job.target_id == episode.id)
            ),
        )
        .limit(1)
    )
    if active_job:
        raise ConflictError("本集仍有视频或合成任务执行中，请完成后再启用新计划")
    if production is None:
        production = EpisodeProduction(
            episode_id=episode.id, workspace_id=episode.workspace_id, settings={}
        )
        session.add(production)
        await session.flush()
    if production.active_plan_id:
        previous = await session.get(EpisodeProductionPlan, production.active_plan_id)
        previous.status = "superseded"
        plan.parent_plan_id = previous.id
    await session.execute(
        update(Shot)
        .where(Shot.scene_id.in_(select(Scene.id).where(Scene.episode_id == episode.id)))
        .values(status=SHOT_STATUS_SUPERSEDED)
    )
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(VideoSegment.plan_id == plan.id)
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    scenes, orders = {}, {}
    for segment, item in zip(segments, assembly["segments"], strict=True):
        for index, source_shot in enumerate(item["shots"], 1):
            scene_key = source_shot["sources"][0]["scene_key"]
            if scene_key not in scenes:
                name = next(
                    (s["text"] for s in source_shot["sources"] if s["kind"] == "scene"),
                    scene_key or f"场景 {len(scenes) + 1}",
                )
                scene = Scene(
                    episode_id=episode.id,
                    owner_id=episode.owner_id,
                    workspace_id=episode.workspace_id,
                    order=len(scenes) + 1,
                    name=name[:255],
                )
                session.add(scene)
                await session.flush()
                scenes[scene_key] = scene
            scene = scenes[scene_key]
            orders[scene_key] = orders.get(scene_key, 0) + 1
            shot = Shot(
                **shot_values(
                    source_shot, scene, orders[scene_key], plan.parameters["planning_record_id"]
                )
            )
            session.add(shot)
            await session.flush()
            session.add(
                VideoSegmentShot(
                    segment_id=segment.id,
                    shot_id=shot.id,
                    workspace_id=episode.workspace_id,
                    order=index,
                    start_time=(source_shot["start_ms"] + item["safe_head_ms"]) / 1000,
                    end_time=(source_shot["end_ms"] + item["safe_head_ms"]) / 1000,
                )
            )
    plan.status = "confirmed"
    plan.confirmed_at = utcnow()
    production.active_plan_id = plan.id
    production.revision += 1
    production.source_script_revision = episode.script_revision
    production.script_stale = False
    production.script_stale_reason = None
    production.final_media_file_id = None
    production.settings = {
        **production.settings,
        "dialogue_cues": [],
        "sound_cues": [],
        "video_model_id": plan.provider_model_id,
        "resolution": segments[0].parameters["resolution"],
        "background_music": assembly["segments"][0]["background_music"],
    }
    parent.payload = {
        **parent.payload,
        "production_activation": {
            "request": baseline,
            "actor_id": actor_id,
            "activated_at": utcnow().isoformat(),
            "plan_id": plan.id,
        },
    }
    await session.flush()
    await verify_shots(session, plan, assembly)
    return {
        **draft,
        "status": "confirmed",
        "active_plan_id": plan.id,
        "production_revision": production.revision,
        "model_called": False,
    }
