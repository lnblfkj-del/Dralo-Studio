"""Read-only, traceable history retrieval. Never rewrite facts or infer resolved hooks."""

import json

from app.core.errors import ConflictError

HISTORY_CHARS = 48000


def compact_continuity_history(facts, *, recent_ids, current_names, outline_text, budget=HISTORY_CHARS):
    records = []
    seen = set()
    for fact in facts:
        value = fact.value or {}
        text = value.get("text") or value.get("thread") or value.get("summary") or value.get("ending_excerpt") or value.get("name")
        if not text:
            continue
        identity = (fact.source_episode_id, fact.source_revision, fact.fact_type, text)
        if identity in seen:
            continue
        seen.add(identity)
        records.append({
            "episode": value.get("episode_number"), "source_episode_id": fact.source_episode_id,
            "revision": fact.source_revision, "type": fact.fact_type,
            "confirmation": fact.confirmation_status, "text": text,
        })

    def pack(rows):
        groups = {}
        for row in rows:
            key = (row["source_episode_id"], row["revision"], row["confirmation"])
            group = groups.setdefault(key, {k: v for k, v in row.items() if k not in {"type", "text"}})
            group.setdefault("facts", {}).setdefault(row["type"], []).append(row["text"])
        return list(groups.values())

    def size(rows):
        return len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))

    packed = pack(records)
    if size(packed) <= budget:
        return packed, {"scope": "all_available_history", "available": len(records), "included": len(records), "omitted": 0}

    # Keep complete recent/confirmed records; rank older evidence, without changing its meaning.
    required = [i for i, row in enumerate(records)
                if row["source_episode_id"] in recent_ids or row["confirmation"] == "confirmed"]
    selected = set(required)
    used = size(pack([records[i] for i in required]))
    if used > budget:
        raise ConflictError("本集近期交接或已确认连续性资料过大，无法安全准备下一集；已完成正文保留，请检查重复的连续性资料。")

    def rank(index):
        row = records[index]
        text = row["text"]
        entity = text.split("：", 1)[0].split(":", 1)[0].strip()
        relevant = any(name and name in text for name in current_names)
        relevant = relevant or (2 <= len(entity) <= 30 and entity in outline_text)
        return (relevant, row["type"] in {"generated_new_hooks", "generated_resolved_hooks", "open_thread"}, index)

    for index in sorted((i for i in range(len(records)) if i not in selected), key=rank, reverse=True):
        cost = size(pack([records[index]])) + 1
        if used + cost <= budget:
            selected.add(index)
            used += cost
    return pack([row for i, row in enumerate(records) if i in selected]), {
        "scope": "retrieved_history_not_complete_state", "available": len(records),
        "included": len(selected), "omitted": len(records) - len(selected),
        "note": "原始记录完整保留。本次仅检索近期、已确认及与本集相关的历史；未提供不代表未发生。悬念是否解决必须结合明确证据，不能仅按新旧覆盖。",
    }
