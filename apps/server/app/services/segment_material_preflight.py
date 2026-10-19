"""Read-only unresolved-material gate at the paid segment boundary."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import CreationSession, Episode


def unresolved_materials(breakdown, episode_number, episode_text, segment_text):
    """Literal matches localize known names; unknown scope stays conservative."""
    pending = []
    for scope in (breakdown.get("review_items_by_scope") or {}).values():
        if episode_number not in scope.get("episode_numbers", []):
            continue
        for row in scope.get("items", []):
            name = str(row.get("name") or "").strip()
            if name and name in episode_text and name not in segment_text:
                continue
            pending.append(name or "未命名素材")
    for row in breakdown.get("candidates") or []:
        if (not row.get("selected", True) or not row.get("needs_review")
                or row.get("confirmed_in_current_scope")
                or episode_number not in row.get("episode_numbers", [])):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name not in episode_text or name in segment_text:
            pending.append(name or "未命名素材")
    for issue in breakdown.get("validation_issues") or []:
        if issue.get("severity") != "blocking" or episode_number not in issue.get("episode_numbers", []):
            continue
        name = str(issue.get("character_name") or "").strip()
        if not name or name not in episode_text or name in segment_text:
            pending.append(name or "来源待核对素材")
    return list(dict.fromkeys(pending))


async def require_segment_materials(session, plan, parent, segment_key):
    from app.models.episode_planning import EpisodePlanningRecord
    from app.services.episode_planning_storage import read_plan

    item = await session.scalar(select(CreationSession).where(CreationSession.project_id == parent.project_id))
    breakdown = (item.settings or {}).get("asset_breakdown") or {} if item else {}
    if not (breakdown.get("review_items_by_scope") or any(row.get("needs_review") for row in breakdown.get("candidates", []))
            or any(row.get("severity") == "blocking" for row in breakdown.get("validation_issues", []))):
        return
    episode = await session.get(Episode, plan.episode_id)
    frozen = read_plan(await session.get(EpisodePlanningRecord, plan.parameters["planning_record_id"]))
    segment = next(s for s in frozen.segments if s.key == segment_key)
    keys = {key for shot in segment.shots for key in shot.source_keys}
    text = "\n".join(unit.text for unit in frozen.sources if unit.key in keys)
    missing = unresolved_materials(breakdown, episode.number, episode.script or "", text)
    if missing:
        raise ConflictError("本片段素材待核对：" + "、".join(missing[:8])
                            + "。请关联或补建素材后重试预检；未调用视频模型，无需重新生成剧本或片段脚本")
