"""Read-only, bounded context from the director's frozen input."""

from copy import deepcopy
from typing import Any

from app.services.episode_director_skill_projection import project_rules

NEIGHBOR_TEXT_BUDGET = 1600
NEIGHBOR_LINE_LIMIT = 6
SEGMENT_HANDOFF_RULES = (
    "导演细化不是续写剧情：表情、视线、呼吸、停顿和摄影可细化，"
    "拿起/放下/交出道具、改变人物站位、确认结果等状态变化必须有本段原文或锁定镜头依据。"
    "本段正文明确发生的动作必须在对应镜头 action 中呈现过程，不能只写进 exit_state、"
    "当作开场已完成的背景或为了保持状态而删去；场景前缀不把同一行后面的动作变成先前事实。"
    "下一段原文描述的结果只约束本段结束，不得倒推为本段进入时已经完成。"
    "原文只有观察或对白时保持已有位置和道具状态，不能把‘给我看看’解释为已经拿起，"
    "不能为填满空台词、配乐或停顿镜头编造新行动。"
    "entry_state 是本段第一动作开始前的状态，exit_state 是本段最后动作完成后的状态；"
    "不能把本段将要发生的动作提前写进进入状态。"
    "同场上一段原文明示的站位、持物、接触和服饰状态持续到原文明示改变为止；"
    "观察、说话、情绪变化或切换景别本身不代表松手、离开、换手或移动。"
    "例如上一段手贴底板，本段只是说话和看水平仪，仍保持手贴底板；"
    "不要补造松手动作，也不要用画面未拍到某状态作为它已改变的依据。"
    "结束状态只能描述本段动作实际完成的结果，"
    "准备、打算或伸手尝试不等于已经走到、拿到或交出。"
    "同场相邻原文明示的人物位置、道具归属和持握侧是衔接约束；"
    "不得新增与下段起点矛盾的移动，也不得为回到原位而编造往返或额外剧情。"
    "合法转场或原文明示状态变化照常保留；缺少证据时保守描述，不把只读节选当完整已生成状态。"
    "每个状态只写有依据的确定事实，未知细节省略或明确未提供；"
    "不得罗列互斥的可能状态（如已放下或仍拿着），不得把下一段尚未发生的动作写成已经完成。"
    "创作动作和状态中不另加台词、旁白、配乐或音效指令，声音只继承冻结来源；"
    "无对白/画外音不等于完全静音，原文明示无声时才约束为无声。"
    "negative_prompt 不得禁止原文对白、旁白、配乐或环境声；禁止新增声音不等于禁止已有声音。"
    "本段对白说完不代表整次通话结束，未写挂断就不补造挂断或通话终止。"
    "同镜头多名说话人的 dialogue_tone 用一条简短共同表演节奏，不分别列角色语气或替某句指定另一人的语气。"
)


def frozen_skill_rules(
    execution: dict[str, Any], *, stage: str | None = None
) -> list[dict[str, Any]]:
    if stage is not None and execution.get("skill_rule_projection") is not None:
        stages = execution["skill_rule_projection"]["stages"]
        if stage not in stages:
            raise ValueError(f"Missing frozen director stage: {stage}")
        return deepcopy(stages[stage])
    rules = [
        {
            "key": item["key"],
            "version": item["version"],
            "instruction": (item.get("snapshot") or {}).get("instruction", ""),
        }
        for item in execution.get("skill_bundle") or []
    ]
    return project_rules(rules, stage) if stage is not None else rules


def _boundary_lines(rows: list[dict[str, Any]], *, previous: bool) -> dict[str, Any]:
    selected = []
    remaining = NEIGHBOR_TEXT_BUDGET
    ordered = reversed(rows) if previous else iter(rows)
    for row in ordered:
        if len(selected) >= NEIGHBOR_LINE_LIMIT or remaining <= 0:
            break
        text = str(row["text"])
        excerpt = text[-remaining:] if previous else text[:remaining]
        selected.append({"line": row["line"], "text": excerpt, "excerpt": excerpt != text})
        remaining -= len(excerpt)
    if previous:
        selected.reverse()
    return {
        "source_lines": selected,
        "available_lines": len(rows),
        "omitted_lines": len(rows) - len(selected),
    }


def segment_handoff_context(
    execution: dict[str, Any], outline: dict[str, Any], segment: dict[str, Any]
) -> dict[str, Any]:
    snapshot = execution["input"]
    segments = outline["segments"]
    position = next(index for index, item in enumerate(segments) if item["key"] == segment["key"])
    sources = {int(item["shot_id"]): item for item in outline["shots"]}
    existing = {int(item["shot_id"]): item for item in snapshot.get("shots") or []}

    def neighbor(index: int, *, previous: bool) -> dict[str, Any] | None:
        if index < 0 or index >= len(segments):
            return None
        item = segments[index]
        ids = item["shot_ids"]
        boundary_scene = sources[int(ids[-1] if previous else ids[0])]["scene_id"]
        current_scene = sources[int(segment["shot_ids"][0] if previous else segment["shot_ids"][-1])]["scene_id"]
        line_ids = {
            line for shot_id in ids for line in sources[int(shot_id)].get("source_lines") or []
        }
        rows = [row for row in snapshot.get("source_lines") or [] if row["line"] in line_ids]
        # Legacy plans may lack line mappings; use only their frozen boundary shots.
        boundary_ids = ids[-2:] if previous else ids[:2]
        boundary_shots = (
            [
                {
                    "shot_id": shot_id,
                    **{
                        field: str(existing[int(shot_id)].get(field) or "")[:400]
                        for field in ("action", "dialogue", "audio_note")
                    },
                }
                for shot_id in boundary_ids
                if int(shot_id) in existing
            ]
            if not rows
            else []
        )
        return {
            "segment_key": item["key"],
            "same_scene": boundary_scene == current_scene,
            "source_kind": "frozen_source_not_generated_result",
            **_boundary_lines(rows, previous=previous),
            "boundary_shot_excerpts": boundary_shots,
        }

    return {
        "source_script_revision": execution.get("source_script_revision"),
        "previous": neighbor(position - 1, previous=True),
        "next": neighbor(position + 1, previous=False),
    }
