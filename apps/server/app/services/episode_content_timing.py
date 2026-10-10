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


def _speech_floor(text: str) -> int:
    # Deliberately generous sanity bound, not an estimate or a target speed.
    # Actual pacing still comes from the explicit performance analysis.
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", text))
    digits = len(re.findall(r"\d", text))
    return math.ceil(1000 * ((cjk + digits) / 8 + words / 5))


def prepare_sources(script: str, catalog: list[dict]) -> PlanningSources:
    rows = [
        {"line": index, "text": text.strip()}
        for index, text in enumerate(script.splitlines(), 1)
        if text.strip()
    ]
    records = parse_sources(rows, catalog)
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
                spoken_text=record.get("spoken_text"),
                scene_key=scene_key,
                source_lines=tuple(record["source_lines"]),
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
        f"source_fingerprint 固定为 {fingerprint(sources)}。\n"
        f"必须各计时一次的来源：{json.dumps(timed, ensure_ascii=False)}\n"
        f"禁止添加 event 但必须保留在 source_keys 的来源：{json.dumps(untimed, ensure_ascii=False)}\n"
        f"结构：{json.dumps(ContentAnalysis.model_json_schema(), ensure_ascii=False)}\n"
        f"正文来源：{sources.model_dump_json()}"
    )


def validate_analysis(sources: PlanningSources, analysis: ContentAnalysis) -> None:
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
