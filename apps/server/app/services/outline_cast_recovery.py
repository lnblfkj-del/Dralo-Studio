"""Review an immutable failed batch; never infer identity from a job title."""

from copy import deepcopy

from app.core.errors import ConflictError
from app.models import CreationArtifact, CreationSession
from app.services.long_form_workflow import OutlineBatch, baseline, baseline_matches, validate_job
from app.services.outline_character_coverage import _name_index
from app.services.structured_output_service import parse_structured_result


async def review_response(db, job, stored):
    params = (job.payload or {}).get("parameters") or {}
    work = await db.get(CreationArtifact, params.get("long_form_work_id")) if params.get("long_form_work_id") else None
    if work is None or work.artifact_type != "long_form_work":
        raise ConflictError("该任务不是可核对角色的分批大纲任务")
    item = await db.get(CreationSession, work.session_id)
    state = work.content
    index = params.get("long_form_step")
    if (item is None or job.project_id != item.project_id or state.get("cursor") != index
            or not isinstance(index, int) or index < 0 or index >= len(state["steps"])
            or state["steps"][index].get("kind") != "outline"
            or not baseline_matches(state["baseline"], await baseline(db, item))):
        raise ConflictError("来源版本或分批进度已变化，不能修改旧批次的角色对应")
    await validate_job(db, job)
    parsed = parse_structured_result(stored.response_text, OutlineBatch, "分批生成结果")
    story = state["parameters"]["story_snapshot"]
    aliases, rows = _name_index(story)
    issues = {}
    for episode in parsed["episodes"]:
        for name in episode.get("characters", []):
            if name.strip().casefold() not in aliases:
                issues.setdefault(name, []).append(episode["number"])
    return {
        "response_sha256": stored.response_sha256,
        "issues": [{"name": name, "episodes": sorted(set(numbers))} for name, numbers in issues.items()],
        "characters": [{"name": name, "role": row.get("role"), "aliases": deepcopy(row.get("aliases") or [])} for name, row in rows.items()],
    }


async def validate_corrections(db, job, stored, corrections, expected_sha):
    review = await review_response(db, job, stored)
    if expected_sha != review["response_sha256"]:
        raise ConflictError("已保存响应已变化，请重新读取角色核对信息")
    unknown = {issue["name"] for issue in review["issues"]}
    canonical = {person["name"] for person in review["characters"]}
    if not corrections or set(corrections) != unknown or any(name not in canonical for name in corrections.values()):
        raise ConflictError("请逐一确认未识别称呼对应的现有角色；真正的新角色需要先修改故事设定")
    return dict(corrections)
