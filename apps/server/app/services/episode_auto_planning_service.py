"""Atomically save a validated text plan; media generation is never started here."""

import json
from copy import deepcopy
from hashlib import sha256

from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError
from app.models import AssetUsage, Episode, EpisodeProductionPlan, Scene, Shot
from app.services import episode_director_service as director
from app.services import segment_plan_service
from app.services.episode_auto_planning_input import _remove_ambiguous_catalog_aliases, enrich_input
from app.services.episode_auto_planning_validation import append_replan_asset_warnings, prepare_result, validate_result
from app.services.episode_empty_scenes import empty_placeholders
from app.services.episode_preparation_gate import require_preparation
from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED, require_idle_shots


async def save_draft(session, job, result):
    previous = job.result or {}
    if previous.get("auto_saved_draft") and previous.get("plan_id"):
        return previous
    episode = await session.get(Episode, job.target_id)
    if episode is None or episode.project_id != job.project_id or episode.owner_id != job.owner_id:
        raise NotFoundError("规划来源分集不存在")
    await require_preparation(session, episode)
    execution = job.payload["director_execution"]
    frozen = deepcopy(execution["input"])
    # Older jobs froze the unfiltered alias cache. Compare derived aliases using
    # today's normalization; raw profile data still detects real user edits.
    _remove_ambiguous_catalog_aliases(frozen.get("asset_catalog") or [])
    production = await director._production(session, episode)
    if (episode.script_revision != execution["source_script_revision"]
            or production.revision != execution["production_revision"]
            or production.active_plan_id != execution.get("parent_plan_id")):
        raise ConflictError("正文或制作计划已变化，已保留模型结果，请核对后重新规划")
    current = await director._input_snapshot(session, episode, allow_empty=True)
    current = await enrich_input(session, episode, production, current)
    frozen_locks = {row["shot_id"] for row in frozen.get("shots") or [] if row.get("is_locked")}
    if any(row["is_locked"] and row["shot_id"] not in frozen_locks for row in current["shots"]):
        raise ConflictError("规划期间有镜头被锁定，不能覆盖；模型结果已保留")
    for key in ("script", "target_duration", "shots", "asset_bindings", "scene_snapshot", "asset_catalog"):
        if current.get(key) != frozen.get(key):
            raise ConflictError("规划期间分镜或资产资料已修改，不能覆盖；模型结果已保留")
    capability = await director.get_video_capabilities(session, execution["video_model_id"])
    # Revalidate preserved text against the current model contract. A corrected
    # capability limit must not force another paid planning call.
    validation_payload = {
        **job.payload,
        "director_execution": {**execution, "input": frozen, "video_model_capability_snapshot": capability},
    }
    validate_result(validation_payload, result)
    prepared, normalized, output = prepare_result(validation_payload, result)
    mapping = {}
    scene_mapping = {}
    sources = {row.shot_id: row for row in output.shot_sources}
    if not frozen["shots"]:
        placeholders = await empty_placeholders(session, episode)
        for placeholder in placeholders:
            await session.delete(placeholder)
        for order, item in enumerate(output.scenes, 1):
            scene = Scene(episode_id=episode.id, owner_id=episode.owner_id, name=item.name, order=order,
                          location=item.location or None, time_of_day=item.time_of_day or None,
                          description=item.description or None)
            session.add(scene)
            await session.flush()
            scene_mapping[item.scene_id] = scene.id
        orders = {}
        for item in output.shots:
            scene_id = scene_mapping[sources[item.shot_id].scene_id]
            orders[scene_id] = orders.get(scene_id, 0) + 1
            shot = Shot(scene_id=scene_id, owner_id=episode.owner_id, order=orders[scene_id])
            session.add(shot)
            await session.flush()
            mapping[item.shot_id] = shot.id
    else:
        await require_idle_shots(session, [int(row["shot_id"]) for row in frozen["shots"]])
        frozen_by_id = {int(row["shot_id"]): row for row in frozen["shots"]}
        scene_mapping = {
            int(row["id"]): int(row["id"])
            for row in frozen.get("scene_snapshot") or []
        }
        for order, item in enumerate(output.shots, start=1):
            previous = frozen_by_id.get(item.shot_id)
            if previous and previous.get("is_locked"):
                mapping[item.shot_id] = item.shot_id
                continue
            shot = Shot(
                scene_id=sources[item.shot_id].scene_id,
                owner_id=episode.owner_id,
                order=order,
                refs=deepcopy((previous or {}).get("refs") or {}),
            )
            session.add(shot)
            await session.flush()
            mapping[item.shot_id] = shot.id
        for row in frozen["shots"]:
            if row.get("is_locked"):
                continue
            historical = await session.get(Shot, row["shot_id"])
            historical.status = SHOT_STATUS_SUPERSEDED
    # Rewrite only typed identity fields, never arbitrary numbers in dialogue or prompts.
    snapshot = prepared["director_execution"]["input"]
    for key in ("shots", "planning_shots", "asset_bindings"):
        snapshot[key] = [
            deepcopy(row) for row in snapshot.get(key) or []
            if row.get("shot_id") is None or row["shot_id"] in mapping
        ]
        for row in snapshot[key]:
            if row.get("shot_id") is not None:
                row["shot_id"] = mapping[row["shot_id"]]
            if row.get("scene_id") in scene_mapping:
                row["scene_id"] = scene_mapping[row["scene_id"]]
    raw = json.loads(normalized["text"])
    for row in raw["shots"]:
        row["shot_id"] = mapping[row["shot_id"]]
        shot = await session.get(Shot, row["shot_id"])
        if shot.is_locked:
            continue
        for field in ("duration", "shot_size", "camera_angle", "camera_movement", "action", "dialogue", "audio_note"):
            setattr(shot, field, row[field])
    for segment in raw["segments"]:
        segment["shot_ids"] = [mapping[value] for value in segment["shot_ids"]]
    await session.flush()
    proposal = director.validate_model_result(
        {**prepared, "auto_prepare": False}, {"text": json.dumps(raw, ensure_ascii=False)},
    )
    append_replan_asset_warnings(proposal, frozen, output)
    frozen_by_id = {int(row["shot_id"]): row for row in frozen.get("shots") or []}
    coverage = []
    for row in output.shot_sources:
        inherited_beat = ((frozen_by_id.get(row.shot_id) or {}).get("refs") or {}).get("beat_id")
        line_key = "-".join(str(line) for line in sorted(set(row.source_lines)))
        if len(line_key) > 40:
            line_key = "hash-" + sha256(line_key.encode("ascii")).hexdigest()[:20]
        beat_id = (
            f"r{episode.script_revision}-lines-{line_key}"
            if row.source_lines else inherited_beat or f"legacy-shot-{row.shot_id}"
        )
        coverage.append({
            **row.model_dump(), "beat_id": beat_id,
            "shot_id": mapping[row.shot_id],
            "scene_id": scene_mapping[row.scene_id],
            "source_script_revision": episode.script_revision,
        })
        saved_shot = await session.get(Shot, mapping[row.shot_id])
        if not saved_shot.is_locked:
            saved_shot.refs = {**(saved_shot.refs or {}), "beat_id": beat_id}
    for source in coverage:
        if source["unresolved_names"]:
            proposal["continuity_report"]["status"] = "blocked"
            proposal["continuity_report"]["issues"].append({
                "code": "unmatched_asset", "shot_id": source["shot_id"],
                "message": "资产匹配待确认：" + "、".join(source["unresolved_names"]),
            })
            for segment in proposal["segments"]:
                if source["shot_id"] in segment["shot_ids"]:
                    segment["refs"].setdefault("unmatched_assets", []).extend(source["unresolved_names"])
    for binding in snapshot["asset_bindings"]:
        if binding.get("shot_id") is None:
            continue
        usage_type = binding.get("usage_type") or binding.get("role") or "reference_image"
        exists = await session.scalar(select(AssetUsage.id).where(
            AssetUsage.asset_id == binding["asset_id"], AssetUsage.shot_id == binding["shot_id"],
            AssetUsage.usage_type == usage_type,
        ))
        if exists is None:
            session.add(AssetUsage(project_id=episode.project_id, episode_id=episode.id,
                                   scene_id=binding["scene_id"], shot_id=binding["shot_id"],
                                   asset_id=binding["asset_id"], asset_version_id=binding.get("asset_version_id"),
                                   usage_type=usage_type))
    source_by_shot = {row["shot_id"]: row for row in coverage}
    for segment in proposal["segments"]:
        segment["refs"]["source_lineage"] = {
            "source_script_revision": episode.script_revision,
            "shots": [{
                "shot_id": shot_id,
                "beat_id": source_by_shot[shot_id]["beat_id"],
                "source_lines": source_by_shot[shot_id]["source_lines"],
            } for shot_id in segment["shot_ids"]],
        }
    plan = await segment_plan_service.create_plan(
        session, episode, expected_production_revision=execution["production_revision"],
        provider_model_id=execution["video_model_id"], model_capability_snapshot=capability,
        parameters={"source": "episode_director", "director_job_id": job.id,
                    "input_fingerprint": job.payload["input_fingerprint"],
                    "skill_bundle": execution["skill_bundle"], "source_coverage": coverage,
                    "continuity_report": proposal["continuity_report"], "shot_plan": proposal["shot_plan"]},
        status="draft", segments=proposal["segments"], source_type="replan" if execution.get("parent_plan_id") else "ai",
        parent_plan_id=execution.get("parent_plan_id"),
        excluded_shot_ids=[] if frozen["shots"] else None,
    )
    lineage = [{
        "segment_id": segment["id"],
        "segment_lineage_key": segment["lineage_key"],
        "shots": [{
            "shot_id": link["shot_id"],
            "beat_id": source_by_shot[link["shot_id"]]["beat_id"],
            "source_lines": source_by_shot[link["shot_id"]]["source_lines"],
        } for link in segment["shots"]],
    } for segment in plan["segments"]]
    plan_row = await session.get(EpisodeProductionPlan, plan["id"])
    plan_row.parameters = {
        **plan_row.parameters,
        "source_lineage": {
            "source_script_revision": episode.script_revision,
            "segments": lineage,
        },
    }
    plan["parameters"] = plan_row.parameters
    proposal.update(proposal_status="confirmed", confirmed_plan_id=plan["id"], source_coverage=coverage,
                    repair_attempted=bool(result.get("_director_repair_attempted")))
    return {"proposal": proposal, "auto_saved_draft": True, "plan_id": plan["id"],
            "usage": result.get("usage") or {}, "action_preview": {
                "kind": "episode_director_plan", "status": "applied", "proposed": proposal,
                "source_revision": episode.script_revision,
            }}
