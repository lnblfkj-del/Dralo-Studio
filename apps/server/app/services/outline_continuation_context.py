"""Source snapshots and bounded context assembly for outline continuation."""

import json
import re
from hashlib import sha256

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import CreationArtifact, Episode, StoryContinuityFact
from app.services import outline_management_service as management

def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def latest_outline(db, item):
    row = await db.scalar(
        select(CreationArtifact)
        .where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == "episode_outline",
            CreationArtifact.status != "superseded",
        )
        .order_by(CreationArtifact.version.desc())
        .limit(1)
    )
    if row is None:
        raise ConflictError("请先建立分集大纲")
    return row


async def source_snapshot(db, item):
    outline = await latest_outline(db, item)
    content = await management.normalize(db, item, outline)
    stories = list(
        (
            await db.scalars(
                select(CreationArtifact)
                .where(
                    CreationArtifact.session_id == item.id,
                    CreationArtifact.artifact_type == "story_bible",
                    CreationArtifact.status == "confirmed",
                )
                .order_by(CreationArtifact.version.desc())
                .limit(1)
            )
        ).all()
    )
    story_source = dict(content.get("story_source") or {})
    if story_source:
        current_story = stories[0] if stories else None
        if (
            current_story is None
            or current_story.id != story_source.get("artifact_id")
            or current_story.version != story_source.get("version")
            or current_story.revision != story_source.get("revision")
        ):
            raise ConflictError(
                "当前分集大纲基于旧故事设定，请先重新生成或确认与当前故事一致的大纲"
            )
    formal = (
        list(
            (
                await db.scalars(
                    select(Episode)
                    .where(
                        Episode.project_id == item.project_id,
                        Episode.status != "archived",
                    )
                    .order_by(Episode.number)
                )
            ).all()
        )
        if item.project_id
        else []
    )
    scripts = [
        {
            "id": e.id,
            "number": e.number,
            "title": e.title,
            "revision": e.script_revision,
            "finalized_revision": e.finalized_script_revision,
            "confirmation_status": (
                "confirmed"
                if e.script_finalized_at and e.finalized_script_revision == e.script_revision
                else "draft"
            ),
            # C2 also needs the current draft to continue the actual written
            # ending. Its status travels with it so the model cannot mistake
            # an unconfirmed draft for locked canon.
            "script": e.script or "",
        }
        for e in formal
    ]
    continuity_facts = (
        list(
            (
                await db.scalars(
                    select(StoryContinuityFact)
                    .where(
                        StoryContinuityFact.project_id == item.project_id,
                        StoryContinuityFact.status == "active",
                    )
                    .order_by(StoryContinuityFact.source_episode_id, StoryContinuityFact.id)
                )
            ).all()
        )
        if item.project_id
        else []
    )
    snapshot = {
        "outline_id": outline.id,
        "revision": outline.revision,
        "content": content,
        "story": stories[0].content if stories else None,
        "story_id": stories[0].id if stories else None,
        "story_revision": stories[0].revision if stories else None,
        "scripts": scripts,
        "continuity_facts": [
            {
                "id": fact.id,
                "episode_id": fact.source_episode_id,
                "source_ref": fact.source_ref,
                "source_revision": fact.source_revision,
                "confirmation_status": fact.confirmation_status,
                "fact_type": fact.fact_type,
                "fact_key": fact.fact_key,
                "value": fact.value,
                "evidence_excerpt": fact.evidence_excerpt,
            }
            for fact in continuity_facts
        ],
        "specs": {
            key: item.settings.get(key) for key in ("episode_count", "episode_duration", "market")
        },
    }
    return outline, snapshot


def build_context(snapshot, request, targets):
    """Full recent/neighbour outlines + finalized scripts; ranked older passages.

    The source catalogue and explicit coverage are persisted for audit. Never
    silently truncate recent full text or pretend lexical retrieval reads all
    historical scripts. Oversize recent context fails before any paid call.
    """
    rows = snapshot["content"]["episodes"]
    nums = {t["number"] for t in targets}
    recent = {r["number"] for r in rows[-3:]}
    if request["mode"] in {"fill", "optimize"}:
        recent |= {r["number"] for r in rows if any(abs(r["number"] - n) <= 1 for n in nums)}
    sources = []
    handoffs = []
    if snapshot["story"]:
        from app.services.long_form_context import story_context
        sources.append(
            {
                "id": "story",
                "text": json.dumps(story_context(snapshot["story"], min(nums), max(nums)), ensure_ascii=False),
                "coverage": "全局规则与目标范围相关设定，不代表完整历史",
            }
        )
    for row in rows:
        if row["number"] in recent:
            sources.append(
                {
                    "id": f"outline:{row['number']}",
                    "text": json.dumps(
                        {
                            k: row.get(k)
                            for k in (
                                "number",
                                "title",
                                "synopsis",
                                "dramatic_goal",
                                "cliffhanger",
                                "characters",
                                "duration_seconds",
                            )
                        },
                        ensure_ascii=False,
                    ),
                    "coverage": "完整大纲",
                }
            )
    older = []
    for row in rows:
        if row["number"] not in recent:
            text = json.dumps(
                {
                    k: row.get(k)
                    for k in (
                        "title",
                        "synopsis",
                        "dramatic_goal",
                        "cliffhanger",
                        "characters",
                        "duration_seconds",
                    )
                },
                ensure_ascii=False,
            )
            older.extend(
                {
                    "id": f"outline:{row['number']}:{i // 1800}",
                    "text": text[i : i + 1800],
                    "coverage": "历史大纲片段",
                }
                for i in range(0, len(text), 1800)
            )
    for script in snapshot["scripts"]:
        if not script["script"]:
            continue
        confirmation_status = script.get("confirmation_status", "confirmed")
        marker_matches = list(re.finditer(r"[【\[]?集尾钩子[】\]]?", script["script"]))
        ending = (
            script["script"][marker_matches[-1].start() :]
            if marker_matches
            else script["script"][-1600:]
        ).strip()
        handoff = {
            "id": f"handoff:{script['number']}:R{script['revision']}",
            "episode_number": script["number"],
            "revision": script["revision"],
            "confirmation_status": confirmation_status,
            "ending_excerpt": ending,
            "has_explicit_cliffhanger": bool(marker_matches),
        }
        handoffs.append(handoff)
        if script["number"] in recent:
            sources.append(
                {
                    "id": handoff["id"],
                    "text": ending,
                    "coverage": (
                        "已确认正文交接"
                        if confirmation_status == "confirmed"
                        else "当前草稿正文交接（未确认）"
                    ),
                }
            )
            sources.append(
                {
                    "id": f"script:{script['number']}:R{script['revision']}",
                    "text": script["script"],
                    "coverage": (
                        "完整已确认正文"
                        if confirmation_status == "confirmed"
                        else "完整当前草稿正文（未确认）"
                    ),
                }
            )
        else:
            older.extend(
                {
                    "id": f"script:{script['number']}:R{script['revision']}:{i // 1800}",
                    "text": script["script"][i : i + 1800],
                    "coverage": "历史正文片段",
                }
                for i in range(0, len(script["script"]), 1800)
            )
    recent_episode_ids = {
        script.get("id")
        for script in snapshot["scripts"]
        if script["number"] in recent and script.get("id") is not None
    }
    for fact in snapshot.get("continuity_facts", []):
        if fact["episode_id"] not in recent_episode_ids:
            continue
        sources.append(
            {
                "id": f"fact:{fact['id']}",
                "text": json.dumps(
                    {
                        "type": fact["fact_type"],
                        "value": fact["value"],
                        "evidence": fact["evidence_excerpt"],
                        "confirmation_status": fact["confirmation_status"],
                    },
                    ensure_ascii=False,
                ),
                "coverage": "可追溯剧情事实",
            }
        )
    query = request["direction"] + request["ending"] + request["fixed_facts"]
    terms = set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2}", query))
    ranked = sorted(older, key=lambda p: sum(p["text"].count(term) for term in terms), reverse=True)
    sources.extend(ranked[:12])
    handoffs.sort(key=lambda item: item["episode_number"])
    latest_handoff = handoffs[-1] if handoffs else None
    context = {
        "sources": sources,
        "outline_index": [
            {
                "number": r["number"],
                "title": r["title"],
                "synopsis_excerpt": r.get("synopsis", "")[:300],
                "cliffhanger": r.get("cliffhanger", ""),
                "characters": list(r.get("characters") or []),
                "duration_seconds": r.get("duration_seconds"),
            }
            for r in rows
        ],
        "coverage": f"完整读取近三集及补全目标邻集；历史资料按要求词语匹配选取 {min(12, len(older))}/{len(older)} 个片段，未入选历史正文不视为已核验。",
        "continuity": {
            "latest_handoff": latest_handoff,
            "recent_handoffs": [item for item in handoffs if item["episode_number"] in recent],
            "requirements": [
                "首个新增分集必须明确处理最近正文交接，不得无说明跳过紧急事件或集尾悬念。",
                "每集必须产生新的状态变化，不得连续重复相同冲突、解决动作和结果。",
                "草稿正文仅作衔接依据，不得描述为用户已确认事实。",
                "按目标时长控制单集事件数量，超载内容必须拆分到后续集。",
            ],
        },
        "warnings": ([] if snapshot["story"] else ["没有已确认故事设定；请在禁改事实中补充约束。"])
        + (
            ["部分正文仍是草稿；已用于实际衔接，但不作为用户确认事实。"]
            if any(
                s["script"] and s.get("confirmation_status") == "draft" for s in snapshot["scripts"]
            )
            else []
        ),
    }
    if len(json.dumps(context, ensure_ascii=False)) > 200000:
        raise ConflictError(
            "近集完整上下文超过 20 万字符安全上限。请减少本次补全集数；未启动收费任务，未截断近集正文。"
        )
    return context


