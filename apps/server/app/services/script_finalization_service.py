"""P2 final-script gates and downstream dependency invalidation."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.team_access import owner_scope

from app.core.errors import ConflictError
from app.models import (
    CreationSession,
    Episode,
    EpisodeProduction,
    Project,
    ProjectAssetLink,
    utcnow,
)


def _episode_status(episode: Episode) -> str:
    if not (episode.script or "").strip():
        return "missing_script"
    if not episode.duration_estimate or episode.duration_estimate < 1:
        return "missing_duration"
    if episode.finalized_script_revision is None:
        return "unconfirmed"
    if episode.finalized_script_revision != episode.script_revision:
        return "stale"
    return "confirmed"


async def _mark_project_asset_scopes_stale(
    session: AsyncSession,
    project_id: int,
    reason: str,
    *,
    episode: Episode | None = None,
) -> None:
    links = list(
        (
            await session.scalars(
                select(ProjectAssetLink).where(ProjectAssetLink.project_id == project_id)
            )
        ).all()
    )
    for link in links:
        data = dict(link.production_data or {})
        scope = dict(data.get("script_scope") or {})
        if scope.get("source_kind") != "script_asset_breakdown":
            continue
        if episode is not None:
            numbers = {int(value) for value in scope.get("episode_numbers") or []}
            revisions = dict(scope.get("source_script_revisions") or {})
            if (
                episode.number not in numbers
                and str(episode.id) not in revisions
            ):
                continue
        scope.update(
            {
                "script_dependency_status": "stale",
                "script_stale_reason": reason,
            }
        )
        data["script_scope"] = scope
        link.production_data = data
        link.production_revision += 1


async def get_project_readiness(
    session: AsyncSession, project: Project
) -> dict[str, Any]:
    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project.id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )
    issues: list[dict[str, Any]] = []
    outline_pending = (project.creation_settings or {}).get("outline_workflow", {}).get("status") == "pending"
    if outline_pending:
        issues.append({"code": "stale_revision", "message": "分集大纲有待确认修改，请先确认大纲，再核对正文。"})
    rows: list[dict[str, Any]] = []
    if not episodes:
        issues.append({
            "code": "no_episodes",
            "message": "项目还没有分集。",
        })
    numbers = [episode.number for episode in episodes]
    if episodes and numbers != list(range(1, len(episodes) + 1)):
        issues.append({
            "code": "non_sequential_numbers",
            "message": "分集编号必须从 1 开始连续排列且不能重复。",
        })
    from app.services.screenplay_preflight import episode_review, project_catalog
    catalog = await project_catalog(session, project.id)
    for episode in episodes:
        state = _episode_status(episode)
        source_review = await episode_review(session, episode, catalog=catalog)
        if source_review["blocking"]:
            issues.append({"episode_id": episode.id, "episode_number": episode.number,
                           "code": "screenplay_source_ambiguous",
                           "message": f"第 {episode.number} 集：" + source_review["errors"][0]["message"]})
        rows.append({
            "episode_id": episode.id,
            "number": episode.number,
            "title": episode.title,
            "script_revision": episode.script_revision,
            "finalized_script_revision": episode.finalized_script_revision,
            "duration_estimate": episode.duration_estimate,
            "status": state,
            "continuity_review_status": episode.continuity_review_status,
            "continuity_review_reason": episode.continuity_review_reason,
        })
        message = {
            "missing_script": f"第 {episode.number} 集正文为空。",
            "missing_duration": f"第 {episode.number} 集尚未填写目标时长。",
            "unconfirmed": f"第 {episode.number} 集当前 V{episode.script_revision} 尚未确认。",
            "stale": (
                f"第 {episode.number} 集已从 V{episode.finalized_script_revision} "
                f"修改为 V{episode.script_revision}, 需要重新确认。"
            ),
        }.get(state)
        if message:
            code = "unconfirmed_revision" if state == "unconfirmed" else (
                "stale_revision" if state == "stale" else state
            )
            issues.append({
                "episode_id": episode.id,
                "episode_number": episode.number,
                "code": code,
                "message": message,
            })
        if episode.continuity_review_status == "conflict":
            issues.append({
                "episode_id": episode.id,
                "episode_number": episode.number,
                "code": "continuity_conflict",
                "message": episode.continuity_review_reason
                or f"第 {episode.number} 集存在尚未处理的一致性冲突。",
            })
        elif episode.continuity_review_status == "needs_review":
            issues.append({
                "episode_id": episode.id,
                "episode_number": episode.number,
                "code": "continuity_stale",
                "message": episode.continuity_review_reason
                or f"第 {episode.number} 集受上游正文修改影响，需要重新检查一致性。",
            })
    continuity_blocked = any(
        issue["code"] in {"continuity_conflict", "continuity_stale"}
        for issue in issues
    )
    can_confirm = not outline_pending and bool(episodes) and numbers == list(range(1, len(episodes) + 1)) and all(
        row["status"] not in {"missing_script", "missing_duration"} for row in rows
    ) and not continuity_blocked and not any(issue["code"] == "screenplay_source_ambiguous" for issue in issues)
    confirmed_count = sum(row["status"] == "confirmed" for row in rows)
    finalization = dict((project.creation_settings or {}).get("script_finalization") or {})
    has_stale = (
        any(row["status"] == "stale" for row in rows)
        or finalization.get("status") == "stale"
        or continuity_blocked
    )
    if not episodes:
        status = "no_episodes"
    elif has_stale or outline_pending:
        status = "stale"
    elif confirmed_count == len(episodes) and not issues:
        status = "confirmed"
    elif can_confirm:
        status = "ready"
    else:
        status = "incomplete"
    return {
        "project_id": project.id,
        "status": status,
        "can_confirm": can_confirm,
        "total_episodes": len(episodes),
        "confirmed_episodes": confirmed_count,
        "confirmed_at": finalization.get("confirmed_at") if status == "confirmed" else None,
        "issues": issues,
        "episodes": rows,
    }


async def require_project_scripts_finalized(
    session: AsyncSession, project_id: int, owner_id: int
) -> list[Episode]:
    project = await session.scalar(
        select(Project).where(Project.id == project_id, owner_scope(Project.owner_id, owner_id))
    )
    if project is None:
        raise ConflictError("项目不存在或无权访问")
    readiness = await get_project_readiness(session, project)
    if readiness["status"] != "confirmed":
        first = readiness["issues"][0]["message"] if readiness["issues"] else "请先确认全集剧本"
        raise ConflictError(f"剧本尚未完成确认: {first}")
    return list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project_id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )


def require_episode_script_finalized(episode: Episode) -> None:
    if episode.status == "archived":
        raise ConflictError("本集已归档，请先恢复分集")
    if not (episode.script or "").strip():
        raise ConflictError("本集正文为空, 请先完成剧本")
    if not episode.duration_estimate or episode.duration_estimate < 1:
        raise ConflictError("本集尚未填写目标时长")
    if episode.finalized_script_revision != episode.script_revision:
        raise ConflictError("本集剧本尚未确认或确认后已修改, 请先重新确认剧本")


async def require_episode_production_ready(session: AsyncSession, episode: Episode) -> None:
    """Gate generation and export on this episode and true project-wide issues."""
    require_episode_script_finalized(episode)
    project = await session.get(Project, episode.project_id)
    readiness = await get_project_readiness(session, project)
    issues = [item for item in readiness["issues"]
              if item.get("episode_id") in (None, episode.id)]
    if issues:
        raise ConflictError(f"本集剧本尚未完成确认: {issues[0]['message']}")
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is not None and production.script_stale:
        raise ConflictError(production.script_stale_reason or "本集制作来源已变化，请重新规划")


async def finalize_project_scripts(
    session: AsyncSession,
    project: Project,
    requested: list[dict[str, int]],
) -> dict[str, Any]:
    if (project.creation_settings or {}).get("outline_workflow", {}).get("status") == "pending":
        raise ConflictError("请先确认分集大纲的修改，再确认正文")
    readiness = await get_project_readiness(session, project)
    continuity_issue = next((
        issue for issue in readiness["issues"]
        if issue["code"] in {"continuity_conflict", "continuity_stale", "screenplay_source_ambiguous"}
    ), None)
    if continuity_issue is not None:
        raise ConflictError(f"剧本一致性尚未通过: {continuity_issue['message']}")
    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project.id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )
    by_id = {episode.id: episode for episode in episodes}
    request_by_id = {item["episode_id"]: item for item in requested}
    if not episodes or set(by_id) != set(request_by_id):
        raise ConflictError("确认列表必须包含项目当前全部分集, 请刷新后重试")
    if [episode.number for episode in episodes] != list(range(1, len(episodes) + 1)):
        raise ConflictError("分集编号必须从 1 开始连续排列")
    for episode in episodes:
        item = request_by_id[episode.id]
        if episode.script_revision != item["expected_script_revision"]:
            raise ConflictError(f"第 {episode.number} 集正文版本已变化, 请刷新后重新确认")
        if not (episode.script or "").strip():
            raise ConflictError(f"第 {episode.number} 集正文为空")
        duration = item["duration_estimate"]
        if duration < 1 or duration > 3600:
            raise ConflictError(f"第 {episode.number} 集目标时长必须为 1 到 3600 秒")

    confirmed_at = utcnow()
    from app.services.story_continuity_service import sync_episode_continuity_records

    snapshot: list[dict[str, int]] = []
    duration_changed: list[tuple[Episode, int | None]] = []
    for episode in episodes:
        item = request_by_id[episode.id]
        was_finalized = episode.finalized_script_revision is not None
        previous_duration = episode.duration_estimate
        result = await session.execute(
            update(Episode)
            .where(
                Episode.id == episode.id,
                Episode.script_revision == item["expected_script_revision"],
            )
            .values(
                duration_estimate=item["duration_estimate"],
                finalized_script_revision=episode.script_revision,
                script_finalized_at=confirmed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConflictError(f"第 {episode.number} 集正文版本已变化, 请刷新后重新确认")
        await session.refresh(episode)
        await sync_episode_continuity_records(
            session, episode, confirmation_status="confirmed"
        )
        # Only a target-duration change against an already confirmed script
        # invalidates derived work. A first confirmation establishes the
        # baseline and must not be reported as stale.
        if was_finalized and previous_duration != episode.duration_estimate:
            duration_changed.append((episode, previous_duration))
        snapshot.append({
            "episode_id": episode.id,
            "number": episode.number,
            "revision": episode.script_revision,
            "duration_estimate": item["duration_estimate"],
        })
        production = await session.scalar(
            select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
        )
        if production is None:
            production = EpisodeProduction(episode_id=episode.id, settings={})
            session.add(production)
        # The first explicit confirmation establishes a baseline for existing
        # projects. Re-confirming an edited script must not pretend that an old
        # scene/clip plan was regenerated from the new revision.
        if (not was_finalized and not production.script_stale) or (
            production.source_script_revision == episode.script_revision
            and not production.script_stale
        ):
            production.source_script_revision = episode.script_revision
            production.script_stale = False
            production.script_stale_reason = None

    settings = dict(project.creation_settings or {})
    settings["script_finalization"] = {
        "status": "confirmed",
        "confirmed_at": confirmed_at.isoformat(),
        "episodes": snapshot,
    }
    project.creation_settings = settings
    await session.flush()
    # Run after the confirmed status is written so the stale markers below are
    # not overwritten by this confirmation.
    for episode, previous_duration in duration_changed:
        await invalidate_episode_duration_dependency(session, episode, previous_duration)
    return await get_project_readiness(session, project)


async def invalidate_script_dependents(
    session: AsyncSession, episode: Episode, previous_revision: int
) -> None:
    """Mark derived work stale after a real script revision change."""

    reason = f"第 {episode.number} 集正文已从 V{previous_revision} 更新为 V{episode.script_revision}"
    episode.continuity_review_status = "unchecked"
    episode.continuity_review_reason = "本集正文已修改，尚未重新检查一致性"
    later_episodes = list((await session.scalars(select(Episode).where(
        Episode.project_id == episode.project_id,
        Episode.number > episode.number,
    ))).all())
    for later in later_episodes:
        if (later.script or "").strip():
            later.continuity_review_status = "needs_review"
            later.continuity_review_reason = f"上游{reason}，本集需要复查"
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is not None and production.source_script_revision is not None:
        production.script_stale = True
        production.script_stale_reason = reason

    project = await session.get(Project, episode.project_id)
    if project is not None:
        settings = dict(project.creation_settings or {})
        finalization = dict(settings.get("script_finalization") or {})
        if finalization:
            finalization.update({
                "status": "stale",
                "stale_episode_id": episode.id,
                "stale_reason": reason,
            })
            settings["script_finalization"] = finalization
        continuity = dict(settings.get("script_continuity_review") or {})
        if continuity:
            affected = sorted({
                *[int(value) for value in continuity.get("affected_episode_numbers", [])],
                *[item.number for item in later_episodes if (item.script or "").strip()],
            })
            continuity.update({
                "status": "stale",
                "stale_reason": reason,
                "affected_episode_numbers": affected,
            })
            settings["script_continuity_review"] = continuity
        project.creation_settings = settings

    sessions = list(
        (
            await session.scalars(
                select(CreationSession).where(CreationSession.project_id == episode.project_id)
            )
        ).all()
    )
    for item in sessions:
        settings = dict(item.settings or {})
        breakdown = dict(settings.get("asset_breakdown") or {})
        if breakdown:
            breakdown.update({"status": "stale", "completed": False, "stale_reason": reason})
            settings["asset_breakdown"] = breakdown
        extractions = dict(settings.get("optional_extractions") or {})
        for key, state in extractions.items():
            extractions[key] = {**dict(state), "status": "stale", "stale_reason": reason}
        if extractions:
            settings["optional_extractions"] = extractions
        item.settings = settings

    await _mark_project_asset_scopes_stale(
        session, episode.project_id, reason, episode=episode
    )
    await session.flush()


async def invalidate_project_structure(
    session: AsyncSession, project_id: int, reason: str
) -> None:
    """Invalidate project-wide derived work after adding, deleting or renumbering episodes."""

    project = await session.get(Project, project_id)
    if project is None:
        return
    settings = dict(project.creation_settings or {})
    finalization = dict(settings.get("script_finalization") or {})
    if not finalization:
        return
    finalization.update({"status": "stale", "stale_reason": reason})
    settings["script_finalization"] = finalization
    project.creation_settings = settings
    sessions = list(
        (await session.scalars(select(CreationSession).where(CreationSession.project_id == project_id))).all()
    )
    for item in sessions:
        item_settings = dict(item.settings or {})
        breakdown = dict(item_settings.get("asset_breakdown") or {})
        if breakdown:
            breakdown.update({"status": "stale", "completed": False, "stale_reason": reason})
            item_settings["asset_breakdown"] = breakdown
        extractions = dict(item_settings.get("optional_extractions") or {})
        for key, state in extractions.items():
            extractions[key] = {**dict(state), "status": "stale", "stale_reason": reason}
        if extractions:
            item_settings["optional_extractions"] = extractions
        item.settings = item_settings
    productions = list(
        (
            await session.scalars(
                select(EpisodeProduction)
                .join(Episode, Episode.id == EpisodeProduction.episode_id)
                .where(Episode.project_id == project_id)
            )
        ).all()
    )
    for production in productions:
        if production.source_script_revision is not None:
            production.script_stale = True
            production.script_stale_reason = reason
    await _mark_project_asset_scopes_stale(session, project_id, reason)
    await session.flush()


async def invalidate_episode_duration_dependency(
    session: AsyncSession, episode: Episode, previous_duration: int | None
) -> None:
    """A confirmed target duration is part of the production planning snapshot."""

    if episode.finalized_script_revision is None:
        return
    reason = (
        f"第 {episode.number} 集目标时长已从 {previous_duration or '未设置'} 秒"
        f"调整为 {episode.duration_estimate or '未设置'} 秒"
    )
    project = await session.get(Project, episode.project_id)
    if project is not None:
        settings = dict(project.creation_settings or {})
        finalization = dict(settings.get("script_finalization") or {})
        if finalization:
            finalization.update({"status": "stale", "stale_reason": reason})
            settings["script_finalization"] = finalization
            project.creation_settings = settings
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is not None and production.source_script_revision is not None:
        production.script_stale = True
        production.script_stale_reason = reason
    sessions = list((await session.scalars(
        select(CreationSession).where(CreationSession.project_id == episode.project_id)
    )).all())
    for item in sessions:
        settings = dict(item.settings or {})
        breakdown = dict(settings.get("asset_breakdown") or {})
        if breakdown:
            breakdown.update({"status": "stale", "completed": False, "stale_reason": reason})
            settings["asset_breakdown"] = breakdown
        extractions = dict(settings.get("optional_extractions") or {})
        for key, state in extractions.items():
            extractions[key] = {**dict(state), "status": "stale", "stale_reason": reason}
        if extractions:
            settings["optional_extractions"] = extractions
        item.settings = settings
    await _mark_project_asset_scopes_stale(
        session, episode.project_id, reason, episode=episode
    )
    await session.flush()


async def revisions_are_current(
    session: AsyncSession, project_id: int, snapshot: dict[str, Any]
) -> bool:
    episodes = list(
        (await session.scalars(select(Episode).where(Episode.project_id == project_id, Episode.status != "archived"))).all()
    )
    return bool(episodes) and all(
        str(episode.id) in snapshot
        and int(snapshot[str(episode.id)]) == episode.script_revision
        and episode.finalized_script_revision == episode.script_revision
        for episode in episodes
    ) and len(snapshot) == len(episodes)
