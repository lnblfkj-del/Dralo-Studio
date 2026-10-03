"""P1 大纲草稿管理：稳定身份、归档恢复、幂等操作和乐观并发控制。"""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import AssetUsage, CreationArtifact, CreationSession, Episode, EpisodeProduction, Scene
from app.schemas.creation import EpisodeOutlineDraftContent, EpisodeOutlineOperation


async def get_outline(db: AsyncSession, item: CreationSession, artifact_id: int) -> CreationArtifact:
    artifact = await db.get(CreationArtifact, artifact_id)
    if artifact is None or artifact.session_id != item.id or artifact.artifact_type != "episode_outline":
        raise NotFoundError("分集大纲不存在")
    return artifact


async def deletion_impacts(db: AsyncSession, item: CreationSession, content: dict) -> dict:
    entries = content.get("episodes", []) + content.get("archived_episodes", [])
    ids = {entry["linked_episode_id"] for entry in entries if entry.get("linked_episode_id")}
    records = {row.id: row for row in (await db.scalars(select(Episode).where(Episode.id.in_(ids), Episode.project_id == item.project_id))).all()} if ids else {}
    owned_ids = list(records)
    scenes = dict((await db.execute(select(Scene.episode_id, func.count(Scene.id)).where(Scene.episode_id.in_(owned_ids)).group_by(Scene.episode_id))).all()) if owned_ids else {}
    assets = dict((await db.execute(select(AssetUsage.episode_id, func.count(AssetUsage.id)).where(AssetUsage.episode_id.in_(owned_ids)).group_by(AssetUsage.episode_id))).all()) if owned_ids else {}
    productions = set((await db.scalars(select(EpisodeProduction.episode_id).where(EpisodeProduction.episode_id.in_(owned_ids)))).all()) if owned_ids else set()
    return {entry["outline_key"]: {
        "linked_episode_id": entry.get("linked_episode_id"),
        "has_script": bool(records.get(entry.get("linked_episode_id")) and records[entry["linked_episode_id"]].script),
        "scene_count": scenes.get(entry.get("linked_episode_id"), 0),
        "asset_usage_count": assets.get(entry.get("linked_episode_id"), 0),
        "has_production": entry.get("linked_episode_id") in productions,
        "formal_data_preserved": True,
    } for entry in entries}


async def normalize(db: AsyncSession, item: CreationSession, artifact: CreationArtifact) -> dict:
    content = deepcopy(artifact.content)
    existing = list((await db.scalars(select(Episode).where(Episode.project_id == item.project_id))).all()) if item.project_id else []
    by_number = {episode.number: episode for episode in existing}
    for episode in content.get("episodes", []):
        # 仅旧条目首次按编号建立关联；新条目必须保持独立身份。
        if not episode.get("outline_key"):
            episode["outline_key"] = str(uuid5(NAMESPACE_URL, f"outline:{item.id}:{artifact.id}:{episode['number']}"))
            linked = by_number.get(episode["number"])
            episode["linked_episode_id"] = linked.id if linked else None
    content.setdefault("archived_episodes", [])
    content.setdefault("operation_receipts", {})
    story = await _latest_story(db, item)
    if story is not None:
        from app.services.outline_character_coverage import review_outline_character_coverage
        content["character_coverage"] = review_outline_character_coverage(story.content, content)
    return content


async def _latest_story(db: AsyncSession, item: CreationSession) -> CreationArtifact | None:
    return await db.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == "story_bible",
            CreationArtifact.status != "superseded",
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )


async def merge_edit(db: AsyncSession, item: CreationSession, artifact: CreationArtifact, incoming: dict) -> dict:
    """普通保存不允许伪造关联或移除归档记录；结构变化走操作 API。"""
    base = await normalize(db, item, artifact)
    incoming = EpisodeOutlineDraftContent.model_validate(incoming).model_dump()
    story = await _latest_story(db, item)
    if story is not None:
        from app.services.outline_character_coverage import canonicalize_outline
        incoming = canonicalize_outline(story.content, incoming)
    old = base.get("episodes", [])
    managed = bool(artifact.content.get("operation_receipts"))
    if managed and [entry.get("outline_key") for entry in incoming["episodes"]] != [entry["outline_key"] for entry in old]:
        raise ConflictError("分集结构已更新，请刷新；新增、删除或排序请使用分集管理操作")
    by_key = {entry["outline_key"]: entry for entry in old}
    by_number = {entry["number"]: entry for entry in old}
    for entry in incoming["episodes"]:
        before = by_key.get(entry.get("outline_key")) if entry.get("outline_key") else by_number.get(entry["number"])
        if entry.get("outline_key") and before is None:
            raise ConflictError("分集标识不属于当前大纲")
        expected_id = before.get("linked_episode_id") if before else None
        if entry.get("linked_episode_id") not in (None, expected_id):
            raise ConflictError("不能更改分集关联")
        entry["outline_key"] = before["outline_key"] if before else str(uuid4())
        entry["linked_episode_id"] = expected_id
        if before and entry.get("synopsis_document") is None and before.get("synopsis") == entry["synopsis"]:
            entry["synopsis_document"] = before.get("synopsis_document")
    result = {**base, "episodes": incoming["episodes"]}
    if story is not None:
        from app.services.outline_character_coverage import review_outline_character_coverage
        result["character_coverage"] = review_outline_character_coverage(story.content, result)
    return result


async def cas_write(db: AsyncSession, artifact: CreationArtifact, content: dict, revision: int) -> None:
    result = await db.execute(update(CreationArtifact).where(
        CreationArtifact.id == artifact.id,
        CreationArtifact.revision == revision,
        CreationArtifact.status == "draft",
    ).values(content=content, revision=revision + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError("大纲已更新或已确认，请刷新后重试")
    await db.refresh(artifact)


async def apply_operation(db: AsyncSession, item: CreationSession, artifact_id: int, payload: EpisodeOutlineOperation) -> CreationArtifact:
    artifact = await get_outline(db, item, artifact_id)
    content = await normalize(db, item, artifact)
    fingerprint_payload = payload.model_dump(exclude={"expected_revision"})
    if payload.count == 1:
        fingerprint_payload.pop("count")
    fingerprint = sha256(json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    receipts = content["operation_receipts"]
    if payload.request_id in receipts:
        if receipts[payload.request_id] != fingerprint:
            raise ConflictError("请求标识已被其他操作使用")
        return artifact
    if artifact.status != "draft" or artifact.revision != payload.expected_revision:
        raise ConflictError("大纲已更新或已确认，请刷新后重试")
    episodes = content["episodes"]
    archived = content["archived_episodes"]
    collection = archived if payload.action in {"restore", "purge"} else episodes
    target = next((entry for entry in collection if entry["outline_key"] == payload.outline_key), None)
    if payload.action != "add" and target is None:
        raise NotFoundError("目标分集不存在")
    from app.core.creation_limits import MAX_EPISODES
    if payload.action in {"add", "duplicate", "restore"} and len(episodes) + (payload.count if payload.action == "add" else 1) > MAX_EPISODES:
        raise ConflictError(f"当前最多支持 {MAX_EPISODES} 集")
    if payload.action == "add":
        duration = payload.duration_seconds or (item.settings or {}).get("episode_duration")
        position = payload.position or len(episodes) + 1
        if position > len(episodes) + 1:
            raise ConflictError("目标位置超出分集范围")
        for offset in range(payload.count):
            episodes.insert(position - 1 + offset, {
                "outline_key": str(uuid4()), "linked_episode_id": None, "number": 1,
                "title": payload.title, "synopsis": "", "dramatic_goal": "", "cliffhanger": "",
                "characters": [], "duration_seconds": duration,
            })
    elif payload.action == "duplicate":
        target = {**deepcopy(target), "outline_key": str(uuid4()), "linked_episode_id": None, "title": (target["title"][:250] + " · 副本")}
    elif payload.action == "delete":
        episodes.remove(target)
        archived.append(target)
    elif payload.action == "restore":
        archived.remove(target)
    elif payload.action == "purge":
        if target.get("linked_episode_id") is not None:
            raise ConflictError("该分集已关联正式正文或资产，只能恢复，不能在此彻底删除")
        archived.remove(target)
    elif payload.action == "move":
        episodes.remove(target)
    if payload.action not in {"add", "delete", "purge"}:
        position = payload.position or len(episodes) + 1
        if position > len(episodes) + 1:
            raise ConflictError("目标位置超出分集范围")
        episodes.insert(position - 1, target)
    for number, entry in enumerate(episodes, 1):
        entry["number"] = number
    EpisodeOutlineDraftContent.model_validate({"episodes": episodes})
    receipts[payload.request_id] = fingerprint
    await cas_write(db, artifact, content, payload.expected_revision)
    from app.services.outline_workflow_service import mark_pending
    await mark_pending(db, item, artifact)
    return artifact


async def validate_links(db: AsyncSession, item: CreationSession, content: dict) -> None:
    """Every existing formal record must have one explicit active/archive disposition."""
    all_entries = content.get("episodes", []) + content.get("archived_episodes", [])
    ids = [entry["linked_episode_id"] for entry in all_entries if entry.get("linked_episode_id")]
    if len(ids) != len(set(ids)):
        raise ConflictError("分集关联重复，请刷新后重试")
    linked = {episode.id: episode for episode in (await db.scalars(select(Episode).where(Episode.id.in_(ids)))).all()} if ids else {}
    for entry in all_entries:
        linked_id = entry.get("linked_episode_id")
        if linked_id and (linked_id not in linked or linked[linked_id].project_id != item.project_id):
            raise ConflictError("关联分集不存在或不属于当前项目")
    existing = list((await db.scalars(select(Episode).where(Episode.project_id == item.project_id))).all()) if item.project_id else []
    covered_ids = {entry.get("linked_episode_id") for entry in all_entries}
    if any(episode.id not in covered_ids and episode.status != "archived" for episode in existing):
        raise ConflictError("大纲缺少已有正式分集，请先恢复对应分集，避免覆盖或遗漏正文")
