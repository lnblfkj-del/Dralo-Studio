"""Source-backed state ledger shared by scene planning and segment assembly."""

import json
import re

STATE_LABELS = {"location": "位置", "posture": "姿态", "injury": "伤势", "costume": "服装",
                "condition": "物品状态", "holder": "持有者", "presence": "在场状态", "lighting": "光线"}


def _state_text(value):
    return re.sub(r"[\s，,。.;；:：]+", "", value or "")

def normalize_continuity(sources, analysis):
    """Canonicalize harmless response differences without changing story facts."""
    if analysis.continuity is None:
        return analysis
    raw = analysis.model_dump(mode="json")
    catalog = {item.asset_id: item for item in sources.identity_catalog}
    for item in raw["continuity"]["identities"]:
        known = catalog.get(item.get("asset_id"))
        if (known and known.kind == item["kind"] == "character"
                and known.key == item["key"] and known.name == item["name"]
                and not known.character_asset_ids
                and item["character_asset_ids"] == [known.asset_id]):
            item["character_asset_ids"] = []
    blocks = {block.key: block for block in analysis.blocks}
    states = {}
    for beat in raw["continuity"]["beats"]:
        block = blocks.get(beat["block_key"])
        if block is None:
            continue
        order = {key: index for index, key in enumerate(block.source_keys)}
        changes = beat["changes"]
        # Reordering independent entities is metadata ordering, not an action rewrite.
        # Never reorder multiple changes to the same entity: cross-field actions may depend on one another.
        if (len({item["entity_key"] for item in changes}) == len(changes)
                and all(item["source_key"] in order for item in changes)):
            changes.sort(key=lambda item: order[item["source_key"]])
        for change in changes:
            key = (change["entity_key"], change["field"])
            previous = states.get(key)
            if previous is not None and (
                change["before"] is None or _state_text(change["before"]) == _state_text(previous)
            ):
                change["before"] = previous
            states[key] = change["after"]
    return type(analysis).model_validate_json(json.dumps(raw, ensure_ascii=False))

def validate_continuity(sources, analysis, *, required=False):
    ledger = analysis.continuity
    if ledger is None:
        if required:
            raise ValueError("缺少整场戏连续性规划，请补齐人物身份、镜头状态变化及正文依据")
        return
    entities = {item.key: item for item in ledger.identities}
    if len(entities) != len(ledger.identities):
        raise ValueError("连续性规划存在重复身份")
    catalog = {item.asset_id: item for item in sources.identity_catalog}
    bound = set()
    for item in ledger.identities:
        if item.asset_id is not None:
            existing = catalog.get(item.asset_id)
            if existing is None or (
                existing.key, existing.name, existing.kind, existing.character_asset_ids
            ) != (item.key, item.name, item.kind, item.character_asset_ids):
                raise ValueError(f"身份 {item.name} 与冻结资产目录不一致")
            if item.asset_id in bound:
                raise ValueError(f"资产 {item.asset_id} 被拆成多个身份")
            bound.add(item.asset_id)
        elif any(item.name in (known.name, *known.aliases) for known in catalog.values()):
            raise ValueError(f"身份 {item.name} 已有资产，不能另建无关联身份")
        elif item.asset_id is None and not any(item.name in unit.text for unit in sources.units):
            raise ValueError(f"新增身份 {item.name} 没有正文依据")
    speaker_ids = {value for unit in sources.units for value in unit.speaker_asset_ids}
    if speaker_ids - bound:
        raise ValueError(f"连续性规划遗漏正文已绑定的角色身份：{sorted(speaker_ids - bound)}")
    if [beat.block_key for beat in ledger.beats] != [block.key for block in analysis.blocks]:
        raise ValueError("连续性规划必须按顺序覆盖全部镜头")
    units = {unit.key: unit for unit in sources.units}
    for issue in ledger.unresolved:
        source = units.get(issue.source_key)
        if source is None or issue.evidence not in source.text:
            raise ValueError("连续性疑点缺少正文依据")
    if ledger.unresolved:
        raise ValueError("原文连续性待确认：" + "；".join(
            f"{issue.source_key}：{issue.problem}" for issue in ledger.unresolved[:5]))
    state = {}
    for block, beat in zip(analysis.blocks, ledger.beats, strict=True):
        source_order = {key: index for index, key in enumerate(block.source_keys)}
        positions = [source_order.get(change.source_key, -1) for change in beat.changes]
        if positions != sorted(positions):
            raise ValueError(f"镜头 {block.key} 的状态变化颠倒了正文动作顺序")
        for change in beat.changes:
            if change.entity_key not in entities:
                raise ValueError(f"镜头 {block.key} 引用了未知身份 {change.entity_key}")
            allowed_kind = {"injury": {"character"}, "costume": {"character"},
                            "holder": {"prop"}, "lighting": {"scene"}}
            if change.field in allowed_kind and entities[change.entity_key].kind not in allowed_kind[change.field]:
                raise ValueError(f"镜头 {block.key} 的{STATE_LABELS[change.field]}记录在错误类型的实体上")
            source = units.get(change.source_key)
            if source is None or change.source_key not in block.source_keys:
                raise ValueError(f"镜头 {block.key} 的状态变化缺少本镜头正文依据")
            if change.evidence not in source.text:
                raise ValueError(f"镜头 {block.key} 的状态依据不是正文原句：{change.source_key}")
            key = (change.entity_key, change.field)
            before = state.get(key)
            # An unrecorded state is not a contradiction with a source-backed debut.
            if before is not None and change.before != before:
                name = entities[change.entity_key].name
                raise ValueError(
                    f"镜头 {block.key}：{name} 的{STATE_LABELS[change.field]}衔接冲突；"
                    f"前序状态为 {before or '尚未确定'}，本镜头却从 {change.before or '未知'} 开始"
                )
            state[key] = change.after


def segment_context(plan, segment_key):
    """Derive boundaries from a single ledger; no independently invented entry state."""
    if plan.continuity is None:
        return None
    selected = next(segment for segment in plan.segments if segment.key == segment_key)
    wanted = {shot.key for shot in selected.shots}
    states, entry, exit_state, beats = {}, None, {}, []
    for beat in plan.continuity.beats:
        if beat.block_key in wanted and entry is None:
            entry = dict(states)
        for change in beat.changes:
            states[(change.entity_key, change.field)] = change.after
        if beat.block_key in wanted:
            beats.append(beat.model_dump(mode="json"))
            exit_state = dict(states)
    if {beat["block_key"] for beat in beats} != wanted:
        raise ValueError("片段缺少对应的整场戏状态记录")
    names = {item.key: item.name for item in plan.continuity.identities}

    def display(values):
        return "\n".join(
            f"{names[entity]} [{entity}] {STATE_LABELS[field]}：{value}"
            for (entity, field), value in sorted(values.items())
        ) or "原文尚未确定状态；不得自行补写剧情事实。"

    return {"entry_state": display(entry or {}), "exit_state": display(exit_state),
            "beats": beats, "identities": [item.model_dump(mode="json") for item in plan.continuity.identities]}


def continuity_prompt(plan, segment_key):
    context = segment_context(plan, segment_key)
    if context is None:
        return ""
    source_keys = {key for shot in next(s for s in plan.segments if s.key == segment_key).shots
                   for key in shot.source_keys}
    scenes = {unit.scene_key for unit in plan.sources if unit.key in source_keys}
    return (
        "\n以下是已经冻结的整场戏与连续性记录。entry_state、exit_state 填写“使用冻结状态”，系统会自动装配；"
        "每个 shot.action 填写“使用冻结动作”，系统使用 beats.staging 装配动作；本次只细化景别、机位与运镜。"
        "摄影必须遵守 beats 的状态变化，不得自行改变人物身份、位置、服装、伤势、道具归属。"
        "身体恢复不代表衣物修复，倒地后不能无依据站起，交接必须有正文依据。"
        "staging 只用于摄影与空间衔接，不得新增剧情。未知状态保持未知。\n"
        f"连续性记录：{json.dumps(context, ensure_ascii=False)}\n"
        f"整场戏原文：{json.dumps([u.model_dump(mode='json') for u in plan.sources if u.scene_key in scenes], ensure_ascii=False)}\n"
    )


def bind_detail_states(plan, detail):
    """Actions and boundaries come from scene planning, not independent rewrites."""
    context = segment_context(plan, detail.segment_key)
    if context is None:
        return detail
    raw = detail.model_dump(mode="json")
    raw.update({key: context[key] for key in ("entry_state", "exit_state")})
    staging = {beat["block_key"]: beat["staging"] for beat in context["beats"]}
    for shot in raw["shots"]:
        if shot["key"] in staging:
            shot["action"] = staging[shot["key"]]
    return type(detail).model_validate_json(json.dumps(raw, ensure_ascii=False))
