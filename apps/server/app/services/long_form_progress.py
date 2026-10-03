"""Public checkpoint summary without prompts, source text, or model responses."""

from sqlalchemy import select
from app.models import CreationArtifact, Job


async def progress(session, item):
    work = await session.scalar(select(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == "long_form_work",
    ).order_by(CreationArtifact.id.desc()).limit(1))
    if work is None:
        return None
    state = work.content
    steps = state.get("steps", [])
    cursor = min(int(state.get("cursor", 0)), len(steps))
    complete = state.get("status") == "complete"
    step = steps[cursor] if cursor < len(steps) else {}
    job = await session.get(Job, state.get("current_job_id"))
    if not job or job.owner_id != item.owner_id or job.deleted_at is not None:
        return None
    newer = await session.scalar(select(Job.id).where(
        Job.target_id == item.id, Job.target_type == job.target_type,
        Job.owner_id == item.owner_id, Job.id > job.id, Job.deleted_at.is_(None),
    ).limit(1))
    if newer is not None:
        return None
    labels = {"source": "读取原文", "reduce": "汇总原文", "frame": "故事框架", "events": "事件脉络", "outline": "分集大纲", "characters": "角色补全"}
    label = labels.get(step.get("kind"), "整理结果")
    if step.get("kind") in {"events", "outline"}:
        label += f" · 第 {step['start']}—{step['end']} 集"
    return {"work_id": work.id, "job_id": job.id, "kind": state.get("kind"),
            "completed_steps": len(steps) if complete else cursor, "total_steps": len(steps),
            "stage": "全部完成" if complete else label,
            "status": "succeeded" if complete else job.status,
            "error": job.error_message if not complete else None}
