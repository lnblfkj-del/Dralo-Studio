"""P3: synchronize outline identities without deleting scripts or production records."""

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import CreationArtifact, CreationSession, Episode, EpisodeProduction, Job, Project

PLANNING_FIELDS = {
    "title": "标题",
    "synopsis": "梗概",
    "characters": "登场角色",
    "dramatic_goal": "戏剧目标",
    "cliffhanger": "集尾悬念",
    "duration_seconds": "规划时长",
}


def _planning_snapshot(content: dict) -> dict[str, dict]:
    return {
        entry["outline_key"]: {
            key: (
                list(entry.get(key) or [])
                if key == "characters"
                else (entry.get(key) or "")
                if key in {"title", "synopsis", "dramatic_goal", "cliffhanger"}
                else deepcopy(entry.get(key))
            )
            for key in PLANNING_FIELDS
        }
        for entry in content.get("episodes", [])
        if entry.get("outline_key")
    }


def episode_context(episode: Episode) -> dict:
    return {
        "id": episode.id,
        "number": episode.number,
        "title": episode.title,
        "synopsis": episode.synopsis,
    }


async def require_episode_context(
    db: AsyncSession, episode: Episode, snapshot: dict | None
) -> None:
    project = await db.get(Project, episode.project_id)
    workflow = (project.creation_settings or {}).get("outline_workflow", {})
    if episode.status == "archived" or workflow.get("status") == "pending":
        raise ConflictError("分集已归档或大纲尚未确认，请先完成大纲确认")
    if (snapshot is not None and snapshot != episode_context(episode)) or (
        snapshot is None and workflow
    ):
        raise ConflictError("分集顺序或大纲内容已变化，请基于最新内容重新生成提案")


async def impact(db: AsyncSession, item: CreationSession, content: dict) -> dict:
    records = {
        row.id: row
        for row in (
            await db.scalars(select(Episode).where(Episode.project_id == item.project_id))
        ).all()
    }
    project = await db.get(Project, item.project_id)
    workflow = (project.creation_settings or {}).get("outline_workflow", {}) if project else {}
    previous_planning = dict(workflow.get("confirmed_planning") or {})
    active = content.get("episodes", [])
    changes = []
    for entry in active:
        formal = records.get(entry.get("linked_episode_id"))
        kinds = []
        if formal is None:
            kinds.append("新增")
        else:
            if formal.status == "archived":
                kinds.append("恢复")
            elif formal.number != entry["number"]:
                kinds.append("排序")
            baseline = previous_planning.get(entry["outline_key"], {})
            comparisons = {
                "title": formal.title or "",
                "synopsis": formal.synopsis or "",
                "duration_seconds": (
                    baseline.get("duration_seconds") if baseline else formal.duration_estimate
                ),
                "characters": list(baseline.get("characters") or [])
                if baseline
                else list(entry.get("characters") or []),
                "dramatic_goal": baseline.get("dramatic_goal", "")
                if baseline
                else entry.get("dramatic_goal", ""),
                "cliffhanger": baseline.get("cliffhanger", "")
                if baseline
                else entry.get("cliffhanger", ""),
            }
            for key, label in PLANNING_FIELDS.items():
                current = entry.get(key)
                if key == "characters":
                    current = list(current or [])
                elif key in {"title", "synopsis", "dramatic_goal", "cliffhanger"}:
                    current = current or ""
                if comparisons[key] != current:
                    kinds.append(label)
        if kinds:
            changes.append(
                {
                    "outline_key": entry["outline_key"],
                    "episode_id": formal.id if formal else None,
                    "number": entry["number"],
                    "title": entry["title"],
                    "changes": kinds,
                    "script_review_required": bool(
                        formal and formal.script and set(kinds) != {"恢复"}
                    ),
                }
            )
    for entry in content.get("archived_episodes", []):
        formal = records.get(entry.get("linked_episode_id"))
        if formal and formal.status != "archived":
            changes.append(
                {
                    "outline_key": entry["outline_key"],
                    "episode_id": formal.id,
                    "number": formal.number,
                    "title": entry["title"],
                    "changes": ["归档"],
                    "script_review_required": False,
                }
            )
    return {
        "changes": changes,
        "structure_changed": any(
            set(row["changes"]) & {"新增", "恢复", "排序", "归档"} for row in changes
        ),
        "formal_data_preserved": True,
    }


async def mark_pending(db: AsyncSession, item: CreationSession, artifact: CreationArtifact) -> None:
    if not item.project_id:
        return
    project = await db.get(Project, item.project_id)
    settings = dict(project.creation_settings or {})
    settings["outline_workflow"] = {
        **settings.get("outline_workflow", {}),
        "status": "pending",
        "artifact_id": artifact.id,
        "version": artifact.version,
    }
    project.creation_settings = settings


async def synchronize(db: AsyncSession, item: CreationSession, artifact: CreationArtifact) -> None:
    from app.services import project_service, script_finalization_service

    project = await project_service.get_project(db, item.project_id, item.owner_id)
    content = deepcopy(artifact.content)
    report = await impact(db, item, content)
    if report["changes"] and await db.scalar(
        select(Job.id)
        .where(
            Job.project_id == project.id,
            Job.status.in_(["queued", "running", "processing", "downloading", "retrying"]),
        )
        .limit(1)
    ):
        raise ConflictError("项目有进行中的任务，请等待任务结束后再确认分集大纲更新")
    records = {
        row.id: row
        for row in (await db.scalars(select(Episode).where(Episode.project_id == project.id))).all()
    }
    settings = dict(project.creation_settings or {})
    had_confirmed_planning = bool(
        (settings.get("outline_workflow") or {}).get("confirmed_planning")
    )
    archive = dict(settings.get("outline_archive") or {})
    active_ids = {entry.get("linked_episode_id") for entry in content["episodes"]}
    if report["structure_changed"]:
        # Parking at -ID avoids transient UNIQUE(project_id, number) collisions.
        # No FK changes: scripts/scenes/assets/production still point to the same ID.
        for formal in records.values():
            if formal.id not in active_ids and formal.status != "archived":
                archive[str(formal.id)] = {"status": formal.status, "number": formal.number}
                formal.status = "archived"
            formal.number = -formal.id
        await db.flush()
    changed_ids = {row["episode_id"] for row in report["changes"] if row["script_review_required"]}
    for entry in content["episodes"]:
        formal = records.get(entry.get("linked_episode_id"))
        duration = entry.get("duration_seconds") or (item.settings or {}).get("episode_duration")
        if formal is None:
            formal = await project_service.create_episode(
                db,
                project,
                {
                    "number": entry["number"],
                    "title": entry["title"],
                    "synopsis": entry["synopsis"],
                    "duration_estimate": duration,
                },
            )
            entry["linked_episode_id"] = formal.id
        else:
            if formal.status == "archived":
                formal.status = archive.pop(str(formal.id), {}).get("status", "draft")
            formal.number = entry["number"]
            formal.title = entry["title"]
            formal.synopsis = entry["synopsis"]
            if duration and (
                not formal.duration_estimate
                or (had_confirmed_planning and formal.duration_estimate != duration)
            ):
                formal.duration_estimate = duration
            if formal.id in changed_ids:
                formal.finalized_script_revision = None
                formal.script_finalized_at = None
                production = await db.scalar(
                    select(EpisodeProduction).where(EpisodeProduction.episode_id == formal.id)
                )
                if production and production.source_script_revision is not None:
                    production.script_stale = True
                    production.script_stale_reason = (
                        "大纲已更新，请核对本集正文与制作内容；原成果已保留"
                    )
    # create_episode may have updated finalization metadata; merge, do not overwrite it.
    settings = dict(project.creation_settings or {})
    settings["outline_archive"] = archive
    settings["outline_workflow"] = {
        "status": "confirmed",
        "artifact_id": artifact.id,
        "version": artifact.version,
        "review_episode_ids": sorted(changed_ids),
        "confirmed_planning": _planning_snapshot(content),
    }
    project.creation_settings = settings
    artifact.content = content
    if report["structure_changed"]:
        await script_finalization_service.invalidate_project_structure(
            db, project.id, "分集大纲已重新确认，请核对正文和制作依赖；已有成果已保留"
        )
    await db.flush()


async def restore_content(
    db: AsyncSession, item: CreationSession, source: CreationArtifact
) -> dict:
    from app.services.outline_management_service import normalize

    content = await normalize(db, item, source)
    latest = await db.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == "episode_outline",
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    if latest and latest.id != source.id:
        if any(
            not entry.get("outline_key") for entry in source.content.get("episodes", [])
        ) and latest.content.get("operation_receipts"):
            raise ConflictError(
                "该旧版本没有稳定分集标识，当前目录已调整；请手动核对内容，不能按旧编号自动恢复"
            )
        newer = await normalize(db, item, latest)
        newer_by_key = {
            entry["outline_key"]: entry for entry in newer["episodes"] + newer["archived_episodes"]
        }
        for entry in content["episodes"] + content["archived_episodes"]:
            # A historical draft may predate first confirmation. Resolve only by
            # stable key, never by a number that may now belong to another episode.
            current = newer_by_key.get(entry["outline_key"])
            if current and current.get("linked_episode_id"):
                entry["linked_episode_id"] = current["linked_episode_id"]
        linked = {
            entry.get("linked_episode_id")
            for entry in content["episodes"] + content["archived_episodes"]
        }
        keys = {
            entry["outline_key"] for entry in content["episodes"] + content["archived_episodes"]
        }
        for entry in newer["episodes"] + newer["archived_episodes"]:
            if entry["outline_key"] not in keys and (
                not entry.get("linked_episode_id") or entry["linked_episode_id"] not in linked
            ):
                content["archived_episodes"].append(deepcopy(entry))
    return content
