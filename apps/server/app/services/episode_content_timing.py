"""Lossless source preparation and validation of natural performance estimates."""

import hashlib
import json
import math
import re

from app.schemas.episode_planning import ContentTiming, SourceUnit
from app.schemas.episode_timing import ContentAnalysis, PlanningSources
from app.services.episode_planning_contract import fingerprint
from app.services.screenplay_source_parser import parse_sources

TIMED_KINDS = {"dialogue", "narration", "action", "sound"}
SPEECH_ROUNDING_MS = 100


def _speech_floor(text: str) -> int:
    # Deliberately generous sanity bound, not an estimate or a target speed.
    # Actual pacing still comes from the explicit performance analysis.
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", text))
    digits = len(re.findall(r"\d", text))
    return math.ceil(1000 * ((cjk + digits) / 8 + words / 5))


def prepare_sources(script: str, catalog: list[dict], interpretations=None) -> PlanningSources:
    rows = [
        {"line": index, "text": text}
        for index, text in enumerate(script.splitlines(), 1)
        if text.strip()
    ]
    records = parse_sources(rows, catalog, interpretations)
    if any(record.get("speaker_kind") == "unbound" for record in records):
        from app.services.screenplay_source_parser import fail
        fail(next(r["line"] for r in records if r.get("speaker_kind") == "unbound"),
             "的声音来源尚未确认", resolvable=True)
    units = []
    scene_key = "scene-start"
    for index, record in enumerate(records):
        if record["kind"] == "scene":
            if all(item["kind"] in {"metadata", "music"} for item in records[:index]):
                scene_key = f"scene-{record['line']}"
            break
    for record in records:
        kind = record["kind"]
        if kind == "scene":
            scene_key = f"scene-{record['line']}"
        elif kind == "dialogue" and record.get("speaker_kind") == "narration":
            kind = "narration"
        elif kind in {"sound_effects", "ambience", "diegetic_music"}:
            kind = "sound"
        units.append(
            SourceUnit(
                key=f"line-{record['line']}",
                kind=kind,
                text=record["text"],
                speaker=record.get("speaker"),
                speaker_asset_ids=tuple(value for value in record.get("speaker_refs", []) if type(value) is int and value > 0),
                spoken_text=record.get("spoken_text"),
                scene_key=scene_key,
                source_lines=tuple(record["source_lines"]),
                voice_source=record.get("voice_source"),
                audio_kind=record["kind"] if kind == "sound" else None,
            )
        )
    return PlanningSources(
        script_fingerprint=hashlib.sha256(script.encode("utf-8")).hexdigest(),
        units=tuple(units),
    )


def analysis_prompt(sources: PlanningSources) -> str:
    # Neither the editorial target nor video limits may bias content duration.
    timed = [unit.key for unit in sources.units if unit.kind in TIMED_KINDS]
    untimed = [unit.key for unit in sources.units if unit.kind not in TIMED_KINDS]
    speech_floors = {unit.key: _speech_floor(unit.spoken_text) for unit in sources.units
                     if unit.kind in {"dialogue", "narration"} and unit.spoken_text}
    return (
        "分析以下已确认正文的自然演绎时间，只返回 episode-timing.v1 JSON。"
        "不创作或改写正文，不规划视频请求数量。一个 block 是一个可连续表演的镜头，"
        "也是最小可剪切表演单位，不是整场戏或一个视频请求。完整对白结束、动作完成、"
        "信息揭示后的反应等天然安全切点应分别形成 block；不要把多个已有安全切点的"
        "单位强行合成一个不可拆的长 block。多个 block 之后由系统组合进同一视频，"
        "因此增加安全切点不等于增加视频请求。不可分割的完整单句仍保留在同一 block。"
        "同场景可含多条来源；不能跨场景。source_keys 按原顺序完整覆盖全部来源一次。"
        "每条对白、旁白、动作和声音各有一个 event；场景标记、元数据和背景音乐建议"
        "附在相应 block 中，不单独增加演绎时间。对白不可在句中切断。"
        "events 只允许 dialogue/narration/action/sound 来源；scene/metadata/music"
        "只放入 source_keys，绝对不能用1毫秒占位 event。events 必须按来源顺序输出。"
        "事件 start_ms 是镜头内开始时间；minimum_ms/estimated_ms/maximum_ms 是自然"
        "演绎的短、中心、长估计，单位整数毫秒，不是加速到极限后的时长。"
        "依据真实读法、角色语速、情绪、停顿及动作说明写 basis。"
        "并行事件时间区间可以重叠，但必须给出 overlap_basis；串行事件不得重叠，"
        "校验重叠采用 start_ms + maximum_ms（不是 estimated_ms）：串行下一事件的"
        "start_ms 必须不小于前面串行事件的 start_ms + maximum_ms。"
        "例如前事件 start_ms=0、estimated_ms=3000、maximum_ms=4500，"
        "串行下一事件至少从4500开始；如从3000开始，必须说明真实并行依据。"
        "不把连续背景声音或配乐长度重复加到每个动作上。"
        "boundary_after 只在完整行动或语句结束且可自然衔接时为 true，并说明 boundary_basis。"
        "不要虚构参考媒体 ID；references 为 []，实际媒体由系统绑定。"
        "必须输出 continuity，先通读整场戏，统一人物、场景、道具身份与动作因果，再安排镜头。"
        "continuity.version 为 narrative-continuity.v1；identities 使用 identity_catalog 中的"
        "key/name/kind/asset_id，别名不得拆成另一人物。没有独立资产的正文实体可用 source- 开头的稳定 key，asset_id 为 null。"
        "beats 按 blocks 顺序逐一覆盖，block_key 一一对应，staging 冻结本镜头完整动作演绎与空间衔接，"
        "明确动作主体、对象与动作先后，不得新增剧情；后续片段细化只调整摄影，不再重写动作。"
        "changes 记录本镜头正文明确支持的状态：location/posture/injury/costume/condition/holder/presence/lighting。"
        "identities 中角色与造型关联 character_asset_ids 必须保留目录值；正文已有 speaker_asset_ids 的角色必须包含。"
        "injury/costume 属于人物，holder 属于道具，lighting 属于场景。"
        "第一次确定某字段 before 为 null；以后 before 必须等于该实体该字段上次 after。"
        "未变化的字段自动继承，不要重复初始化；未明确的状态保持未知，不猜测。"
        "每项变化 source_key 必须属于本 block，evidence 必须逐字摘录对应正文。"
        "伤势和服装分开记录：身体恢复不代表破衣修复；倒地后不能无依据站起；道具转移必须有交接依据。"
        "镜头先后必须保持动作因果，换场也不能重置人物身份、伤势或道具归属。"
        "如果正文有实质因果矛盾、关键动作缺口，无法在不新增剧情的前提下确定衔接，"
        "放入 continuity.unresolved（source_key、逐字 evidence、具体 problem），不得编造修复。"
        "普通未交代的细节保持未知即可，不作为 unresolved；没有实质矛盾则 unresolved 为 []。"
        f"source_fingerprint 固定为 {fingerprint(sources)}。\n"
        f"必须各计时一次的来源：{json.dumps(timed, ensure_ascii=False)}\n"
        f"禁止添加 event 但必须保留在 source_keys 的来源：{json.dumps(untimed, ensure_ascii=False)}\n"
        "以下仅是对白最低校验边界，不是自然语速目标；minimum_ms 不得低于它，"
        "estimated_ms 应按真实停顿和情绪留足时间，不要四舍五入到边界之下："
        f"{json.dumps(speech_floors, ensure_ascii=False)}\n"
        f"结构：{json.dumps(ContentAnalysis.model_json_schema(), ensure_ascii=False)}\n"
        f"正文来源：{sources.model_dump_json()}"
    )


def constrain_speech_minima(sources: PlanningSources, analysis: ContentAnalysis):
    """Constrain lower bounds and reconcile one 100ms estimation rounding step."""
    by_key = {unit.key: unit for unit in sources.units}
    corrections = []
    blocks = []
    for block in analysis.blocks:
        events = []
        for event in block.events:
            source = by_key.get(event.source_key)
            if source and source.kind in {"dialogue", "narration"} and source.spoken_text:
                floor = _speech_floor(source.spoken_text)
                if (0 < floor - event.estimated_ms <= SPEECH_ROUNDING_MS
                        and floor <= event.maximum_ms):
                    corrections.append({"source_key": source.key, "minimum_before_ms": event.minimum_ms,
                                        "minimum_after_ms": floor, "estimated_before_ms": event.estimated_ms,
                                        "estimated_after_ms": floor, "reason": "speech_estimate_rounding"})
                    event = event.model_copy(update={"minimum_ms": floor, "estimated_ms": floor})
                elif event.minimum_ms < floor <= event.estimated_ms:
                    corrections.append({"source_key": source.key, "minimum_before_ms": event.minimum_ms,
                                        "minimum_after_ms": floor, "reason": "speech_sanity_floor"})
                    event = event.model_copy(update={"minimum_ms": floor})
            events.append(event)
        blocks.append(block.model_copy(update={"events": tuple(events)}))
    return analysis.model_copy(update={"blocks": tuple(blocks)}), corrections


def validate_analysis(sources: PlanningSources, analysis: ContentAnalysis) -> None:
    from app.services.narrative_continuity import validate_continuity
    validate_continuity(sources, analysis)
    if analysis.source_fingerprint != fingerprint(sources):
        raise ValueError("Content analysis belongs to a different script snapshot")
    by_key = {unit.key: unit for unit in sources.units}
    keys = [unit.key for unit in sources.units]
    if len(by_key) != len(keys):
        raise ValueError("Duplicate source identity")
    if [key for block in analysis.blocks for key in block.source_keys] != keys:
        raise ValueError("Analysis must cover all sources exactly once in order")
    for block in analysis.blocks:
        units = [by_key[key] for key in block.source_keys]
        if {unit.scene_key for unit in units} != {block.scene_key}:
            raise ValueError("Content block cannot cross scene boundaries")
        if [event.source_key for event in block.events] != [
            unit.key for unit in units if unit.kind in TIMED_KINDS
        ]:
            raise ValueError("Every performance event must be timed exactly once in source order")
        starts = [event.start_ms for event in block.events]
        if starts != sorted(starts):
            raise ValueError("Performance event order cannot reverse the source")
        speech = []
        for event in block.events:
            source = by_key[event.source_key]
            if source.kind in {"dialogue", "narration"}:
                if source.spoken_text is None:
                    raise ValueError("Speech timing requires parsed spoken text")
                if event.minimum_ms < _speech_floor(source.spoken_text):
                    raise ValueError(f"Speech timing is implausibly short: {source.key}")
                for previous_event, previous_source in speech:
                    if (
                        source.speaker == previous_source.speaker
                        and event.start_ms < previous_event.start_ms + previous_event.estimated_ms
                    ):
                        raise ValueError("The same speaker cannot speak two source lines at once")
                speech.append((event, source))
    for previous, current in zip(analysis.blocks, analysis.blocks[1:], strict=False):
        if previous.scene_key != current.scene_key and not previous.boundary_after:
            raise ValueError("Cross-scene continuity needs an explicit safe boundary")


def content_timing(
    sources: PlanningSources,
    analysis: ContentAnalysis,
    editorial_target_ms: int | None,
) -> ContentTiming:
    validate_analysis(sources, analysis)
    return ContentTiming(
        editorial_target_ms=editorial_target_ms,
        target_policy="approximate",
        estimated_min_ms=sum(b.duration("minimum_ms") for b in analysis.blocks),
        estimated_ms=sum(b.duration("estimated_ms") for b in analysis.blocks),
        estimated_max_ms=sum(b.duration("maximum_ms") for b in analysis.blocks),
        basis="逐镜头取并行事件结束时间的最大值，顺序镜头累加；演绎估计并非成品实测。"
        f"分析指纹：{fingerprint(analysis)}",
    )
