"""C4 evidence-backed script checks and explicitly applied local repairs."""

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ProviderError
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    Episode,
    Job,
    Project,
    utcnow,
)
from app.schemas.creation import (
    EpisodeScriptOptimizationResult,
    ScriptContinuityCheckResult,
)
from app.services import job_service, script_version_service
from app.services.creation_agent_service import _attach_agent_execution, _resolve_agent_execution
from app.services.creation_session_service import parse_structured_result

TARGET_SCRIPT_CONTINUITY_CHECK = "script_continuity_check"
TARGET_SCRIPT_CONTINUITY_REPAIR = "script_continuity_repair"
ACTIVE_JOB_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_RETRYING,
}


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", "", value)


async def get_review(session: AsyncSession, project: Project) -> dict[str, Any]:
    review = dict((project.creation_settings or {}).get("script_continuity_review") or {})
    if review:
        if review.get("status") == "running" and review.get("job_id"):
            job = await session.get(Job, int(review["job_id"]))
            if job is not None and job.status in {"cancelled", "failed"}:
                previous = dict((job.payload or {}).get("previous_continuity_review") or {})
                review = {
                    **previous,
                    "status": previous.get("status") or "unchecked",
                    "summary": previous.get("summary") or (
                        "一致性检查已取消，正文未被修改。" if job.status == "cancelled"
                        else "一致性检查未完成，正文未被修改。"
                    ),
                    "issues": list(previous.get("issues") or []),
                    "job_id": job.id,
                }
                if job.status == "failed":
                    review["last_error"] = job.error_message or "一致性检查失败"
                settings = dict(project.creation_settings or {})
                settings["script_continuity_review"] = review
                project.creation_settings = settings
                await session.flush()
            elif job is not None and job.status == "succeeded":
                completed = dict((job.result or {}).get("continuity_report") or {})
                if completed:
                    review = completed
                    settings = dict(project.creation_settings or {})
                    settings["script_continuity_review"] = review
                    project.creation_settings = settings
                    await session.flush()
        return review
    episodes = list((await session.scalars(
        select(Episode).where(Episode.project_id == project.id).order_by(Episode.number)
    )).all())
    return {
        "status": "unchecked",
        "summary": "尚未检查跨集一致性。",
        "issues": [],
        "source_revisions": {},
        "affected_episode_numbers": [item.number for item in episodes if (item.script or "").strip()],
        "job_id": None,
    }


async def accept_issue(
    session: AsyncSession, project: Project, issue_id: str, reason: str, actor_id: int
) -> dict[str, Any]:
    review = await get_review(session, project)
    if review.get("status") in {"unchecked", "running", "stale", "failed"}:
        raise ConflictError("当前检查结果不可审阅，请先重新检查一致性")
    issues = [dict(item) for item in review.get("issues", [])]
    issue = next((item for item in issues if item.get("id") == issue_id), None)
    if issue is None:
        raise NotFoundError("一致性问题不存在")
    episodes = list((await session.scalars(
        select(Episode).where(Episode.project_id == project.id).order_by(Episode.number)
    )).all())
    by_number = {item.number: item for item in episodes}
    revisions = {int(k): int(v) for k, v in dict(review.get("source_revisions") or {}).items()}
    stale = [number for number, revision in revisions.items() if number not in by_number or by_number[number].script_revision != revision]
    if stale:
        raise ConflictError(f"正文版本已变化，请重新检查一致性：{stale}")
    issue["resolution"] = {
        "kind": "accepted",
        "reason": reason.strip(),
        "actor_id": actor_id,
        "resolved_at": utcnow().isoformat(),
    }
    unresolved = [item for item in issues if not item.get("resolution")]
    issue_status: dict[int, str] = {}
    reasons: dict[int, list[str]] = {}
    for item in unresolved:
        for number in item.get("episodes", []):
            level = "conflict" if item.get("severity") == "conflict" else "warning"
            if issue_status.get(number) != "conflict":
                issue_status[number] = level
            reasons.setdefault(number, []).append(str(item.get("summary") or ""))
    for number in revisions:
        episode = by_number[number]
        episode.continuity_review_status = issue_status.get(number, "current")
        episode.continuity_review_reason = "；".join(filter(None, reasons.get(number, []))) or None
    review.update({
        "status": "conflict" if any(item.get("severity") == "conflict" for item in unresolved) else ("warning" if unresolved else "passed"),
        "issues": issues,
        "summary": f"已审阅 {len(issues) - len(unresolved)} 项，剩余 {len(unresolved)} 项。",
    })
    settings = dict(project.creation_settings or {})
    settings["script_continuity_review"] = review
    project.creation_settings = settings
    await session.flush()
    return review


async def accept_manual_review(
    session: AsyncSession, project: Project, episode_revisions: dict[int, int], reason: str, actor_id: int
) -> dict[str, Any]:
    episodes = list((await session.scalars(
        select(Episode).where(Episode.project_id == project.id, Episode.status != "archived")
    )).all())
    by_id = {item.id: item for item in episodes}
    if set(by_id) != set(episode_revisions):
        raise ConflictError("人工复核必须覆盖当前全部分集，请刷新后重试")
    changed = [item.number for item in episodes if item.script_revision != episode_revisions[item.id]]
    if changed:
        raise ConflictError(f"正文版本已变化，请刷新后重新复核：{changed}")
    for episode in episodes:
        if episode.continuity_review_status == "needs_review":
            episode.continuity_review_status = "current"
            episode.continuity_review_reason = None
    review = await get_review(session, project)
    review.update({
        "status": "warning",
        "summary": "当前版本已由创作者人工复核。",
        "issues": [],
        "source_revisions": {str(item.number): item.script_revision for item in episodes},
        "affected_episode_numbers": sorted(item.number for item in episodes),
        "manual_review": {"reason": reason.strip(), "actor_id": actor_id, "reviewed_at": utcnow().isoformat()},
    })
    settings = dict(project.creation_settings or {})
    settings["script_continuity_review"] = review
    project.creation_settings = settings
    await session.flush()
    return review


async def create_check_job(
    session: AsyncSession,
    project: Project,
    episode_numbers: list[int],
) -> Job:
    active = await session.scalar(select(Job.id).where(
        Job.owner_id == project.owner_id,
        Job.project_id == project.id,
        Job.target_type == TARGET_SCRIPT_CONTINUITY_CHECK,
        Job.status.in_(ACTIVE_JOB_STATUSES),
    ))
    if active is not None:
        raise ConflictError("当前项目正在检查剧本一致性")
    episodes = list((await session.scalars(
        select(Episode).where(Episode.project_id == project.id).order_by(Episode.number)
    )).all())
    requested = set(episode_numbers)
    selected = [
        item for item in episodes
        if (not requested or item.number in requested) and (item.script or "").strip()
    ]
    missing = sorted(requested.difference(item.number for item in selected))
    if missing:
        raise ConflictError(f"分集不存在或缺少正文：{missing}")
    if len(selected) < 2:
        raise ConflictError("至少需要两集正文才能检查跨集一致性")
    if len(selected) > 20:
        raise ConflictError("单次最多检查 20 集，请分段检查")

    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "script_continuity_check"
    )
    revisions = {str(item.number): item.script_revision for item in selected}
    documents = [
        {
            "episode_number": item.number,
            "title": item.title,
            "synopsis": item.synopsis,
            "source_revision": item.script_revision,
            "script": item.script,
        }
        for item in selected
    ]
    schema = ScriptContinuityCheckResult.model_json_schema()
    prompt = f"""{instruction_prefix}

你是剧本连续性审校员。检查以下已保存正文中的跨集冲突，只输出符合 Schema 的 JSON，不要 Markdown。
每个问题必须指定冲突类型、严重度、涉及集数、建议修订集，并提供一至六条逐字证据。
证据 quote 必须原样出现在对应集正文中，不得改写、概括或捏造；source_revision 必须使用输入版本。
只报告有证据的问题，不把风格偏好当作事实冲突。优先检查人物姓名/身份/关系、伤势和位置、道具归属、时间地点、因果、重复事件及未回收悬念。
建议应是最小范围的局部修改，不得提出重写整剧。没有问题时 issues 返回空数组。

输出 Schema：{json.dumps(schema, ensure_ascii=False)}
待检查正文：{json.dumps(documents, ensure_ascii=False)}"""
    job = await job_service.create_text_job(
        session,
        project.owner_id,
        provider_model_id=model.id,
        prompt=prompt,
        project_id=project.id,
        parameters={
            "source_revisions": revisions,
            "episode_numbers": [item.number for item in selected],
        },
    )
    job.target_type = TARGET_SCRIPT_CONTINUITY_CHECK
    job.target_id = project.id
    job.max_attempts = 1
    _attach_agent_execution(job, execution)
    settings = dict(project.creation_settings or {})
    previous_review = dict(settings.get("script_continuity_review") or {})
    job.payload = {**job.payload, "previous_continuity_review": previous_review}
    settings["script_continuity_review"] = {
        **previous_review,
        "status": "running",
        "summary": "正在检查跨集一致性。",
        "issues": list(previous_review.get("issues") or []),
        "source_revisions": revisions,
        "affected_episode_numbers": [item.number for item in selected],
        "job_id": job.id,
    }
    project.creation_settings = settings
    await session.flush()
    return job


async def mark_check_failed(session: AsyncSession, job: Job) -> None:
    if job.target_type != TARGET_SCRIPT_CONTINUITY_CHECK or job.project_id is None:
        return
    project = await session.get(Project, job.project_id)
    if project is None:
        return
    previous = dict((job.payload or {}).get("previous_continuity_review") or {})
    review = {
        **previous,
        "status": previous.get("status") or ("unchecked" if job.status == "cancelled" else "failed"),
        "summary": previous.get("summary") or ("一致性检查已取消，正文未被修改。" if job.status == "cancelled" else "一致性检查未完成，正文未被修改。"),
        "issues": list(previous.get("issues") or []),
        "job_id": job.id,
    }
    if job.status != "cancelled":
        review["last_error"] = job.error_message or "一致性检查失败"
    settings = dict(project.creation_settings or {})
    settings["script_continuity_review"] = review
    project.creation_settings = settings
    await session.flush()


async def finalize_check(
    session: AsyncSession, job: Job, provider_result: dict[str, Any]
) -> None:
    project = await session.get(Project, job.project_id)
    if project is None or project.owner_id != job.owner_id:
        raise NotFoundError("项目不存在")
    report = parse_structured_result(
        str(provider_result.get("text", "")),
        ScriptContinuityCheckResult,
        "剧本一致性检查结果",
    )
    parameters = dict(job.payload.get("parameters") or {})
    source_revisions = {
        int(number): int(revision)
        for number, revision in dict(parameters.get("source_revisions") or {}).items()
    }
    episodes = list((await session.scalars(select(Episode).where(
        Episode.project_id == project.id,
        Episode.number.in_(source_revisions),
    ))).all())
    by_number = {item.number: item for item in episodes}
    stale = [
        number for number, revision in source_revisions.items()
        if number not in by_number or by_number[number].script_revision != revision
    ]
    if stale:
        raise ConflictError(f"检查期间正文版本已变化：{stale}")

    for issue in report["issues"]:
        evidence_numbers: set[int] = set()
        for evidence in issue["evidence"]:
            number = int(evidence["episode_number"])
            episode = by_number.get(number)
            if episode is None or int(evidence["source_revision"]) != source_revisions[number]:
                raise ProviderError(f"问题 {issue['id']} 引用了输入范围外或版本错误的证据")
            quote = _normalize_evidence(str(evidence["quote"]))
            if not quote or quote not in _normalize_evidence(episode.script or ""):
                raise ProviderError(f"问题 {issue['id']} 的证据未出现在第 {number} 集原文中")
            evidence_numbers.add(number)
        if not evidence_numbers.issubset(set(issue["episodes"])):
            raise ProviderError(f"问题 {issue['id']} 的涉及集数未覆盖证据来源")
        if int(issue["repair_episode_number"]) not in source_revisions:
            raise ProviderError(f"问题 {issue['id']} 的建议修订集不在检查范围内")

    issue_status: dict[int, str] = {}
    reasons: dict[int, list[str]] = {}
    for issue in report["issues"]:
        for number in issue["episodes"]:
            if number not in source_revisions:
                continue
            level = "conflict" if issue["severity"] == "conflict" else "warning"
            if issue_status.get(number) != "conflict":
                issue_status[number] = level
            reasons.setdefault(number, []).append(issue["summary"])
    for number, episode in by_number.items():
        episode.continuity_review_status = issue_status.get(number, "current")
        episode.continuity_review_reason = "；".join(reasons.get(number, [])) or None

    review = {
        "status": "conflict" if any(
            issue["severity"] == "conflict" for issue in report["issues"]
        ) else ("warning" if report["issues"] else "passed"),
        "summary": report["summary"],
        "issues": report["issues"],
        "source_revisions": {str(key): value for key, value in source_revisions.items()},
        "affected_episode_numbers": sorted(source_revisions),
        "job_id": job.id,
        "checked_at": utcnow().isoformat(),
    }
    settings = dict(project.creation_settings or {})
    settings["script_continuity_review"] = review
    project.creation_settings = settings
    provider_result["continuity_report"] = review
    await session.flush()


async def create_repair_job(
    session: AsyncSession,
    project: Project,
    episode: Episode,
    issue_id: str,
    instruction: str,
) -> Job:
    review = await get_review(session, project)
    if review.get("status") in {"unchecked", "running", "stale"}:
        raise ConflictError("请先基于当前正文完成一致性检查")
    issue = next((item for item in review.get("issues", []) if item.get("id") == issue_id), None)
    if issue is None:
        raise NotFoundError("一致性问题不存在")
    if issue.get("resolution"):
        raise ConflictError("该问题已完成审阅")
    if int(issue.get("repair_episode_number") or 0) != episode.number:
        raise ConflictError(f"该问题建议修订第 {issue.get('repair_episode_number')} 集")
    expected = int(dict(review.get("source_revisions") or {}).get(str(episode.number), -1))
    if expected != episode.script_revision:
        raise ConflictError("正文已变化，请重新检查一致性")
    active = await session.scalar(select(Job.id).where(
        Job.owner_id == episode.owner_id,
        Job.target_type == TARGET_SCRIPT_CONTINUITY_REPAIR,
        Job.target_id == episode.id,
        Job.status.in_(ACTIVE_JOB_STATUSES),
    ))
    if active is not None:
        raise ConflictError("本集已有局部修订提案正在生成")
    siblings = list((await session.scalars(select(Episode).where(
        Episode.project_id == episode.project_id,
        Episode.number.in_([episode.number - 1, episode.number + 1]),
    ).order_by(Episode.number))).all())
    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "script_continuity_repair"
    )
    schema = EpisodeScriptOptimizationResult.model_json_schema()
    prompt = f"""{instruction_prefix}

你是剧本局部修订编辑。只修复指定的一项跨集冲突，输出符合 Schema 的 JSON，不要 Markdown。
保留当前集未涉及问题的剧情、场景、对白和集尾钩子；不得重写其他分集，不得顺带扩写新剧情。
这是唯一一轮自动修订提案。返回完整的本集正文供人工对照，确认前系统不会写入。
用户补充要求：{instruction.strip() or "无"}
指定问题：{json.dumps(issue, ensure_ascii=False)}
相邻正文：{json.dumps([{"episode_number": item.number, "script": item.script} for item in siblings], ensure_ascii=False)}
当前集：{json.dumps({"episode_number": episode.number, "title": episode.title, "synopsis": episode.synopsis, "script": episode.script}, ensure_ascii=False)}
输出 Schema：{json.dumps(schema, ensure_ascii=False)}"""
    job = await job_service.create_text_job(
        session,
        episode.owner_id,
        provider_model_id=model.id,
        prompt=prompt,
        project_id=episode.project_id,
        parameters={
            "issue_id": issue_id,
            "repair_round": 1,
            "source_revision": episode.script_revision,
            "source_script": episode.script or "",
        },
    )
    job.target_type = TARGET_SCRIPT_CONTINUITY_REPAIR
    job.target_id = episode.id
    job.max_attempts = 1
    _attach_agent_execution(job, execution)
    await session.flush()
    return job


async def finalize_repair(
    session: AsyncSession, job: Job, provider_result: dict[str, Any]
) -> None:
    episode = await session.get(Episode, job.target_id)
    if episode is None or episode.owner_id != job.owner_id:
        raise NotFoundError("分集不存在")
    parameters = dict(job.payload.get("parameters") or {})
    source_revision = int(parameters.get("source_revision", -1))
    if episode.script_revision != source_revision:
        raise ConflictError("正文已变化，局部修订提案已过期")
    proposal = parse_structured_result(
        str(provider_result.get("text", "")),
        EpisodeScriptOptimizationResult,
        "剧本局部修订结果",
    )
    provider_result["proposal"] = {
        **proposal,
        "issue_id": parameters.get("issue_id"),
        "source_revision": source_revision,
    }
    provider_result["action_preview"] = {
        "kind": "episode_script",
        "status": "pending",
        "title": f"第 {episode.number} 集局部修订",
        "summary": proposal["reply"],
        "target_type": TARGET_SCRIPT_CONTINUITY_REPAIR,
        "source": {
            "id": episode.id,
            "revision": source_revision,
            "title": episode.title,
            "synopsis": episode.synopsis,
            "script": parameters.get("source_script", ""),
        },
        "proposed": proposal,
        "trace": {"job_id": job.id, "provider": job.provider, "model": job.model},
    }


async def apply_repair(
    session: AsyncSession,
    episode: Episode,
    job_id: int,
    expected_revision: int,
    actor_id: int,
) -> Episode:
    job = await job_service.get_job(session, job_id, actor_id)
    if (
        job.target_type != TARGET_SCRIPT_CONTINUITY_REPAIR
        or job.target_id != episode.id
        or job.status != "succeeded"
    ):
        raise ConflictError("局部修订提案尚未完成或不属于本集")
    result = dict(job.result or {})
    preview = dict(result.get("action_preview") or {})
    if preview.get("status") == "applied":
        return episode
    proposal = dict(result.get("proposal") or {})
    source_revision = int(proposal.get("source_revision", -1))
    if source_revision != expected_revision or episode.script_revision != expected_revision:
        raise ConflictError("正文已变化，请重新检查并生成局部修订")
    updated = await script_version_service.save_script(
        session,
        episode,
        str(proposal.get("script") or ""),
        expected=expected_revision,
        actor_id=actor_id,
        note=f"一致性局部修订：{proposal.get('issue_id')}",
        source="ai",
    )
    updated.title = str(proposal.get("title") or updated.title or "")
    updated.synopsis = str(proposal.get("synopsis") or updated.synopsis or "")
    from app.services.story_continuity_service import sync_episode_continuity_records

    await sync_episode_continuity_records(session, updated, confirmation_status="draft")
    preview.update({"status": "applied", "applied_revision": updated.script_revision})
    job.result = {
        **result,
        "applied_revision": updated.script_revision,
        "action_preview": preview,
    }
    await session.flush()
    return updated
