"""N4 剧集结构运行时策略。

结构合同只保存用户确认的事实；本模块把这些事实编译成生成阶段共用的确定性规则。
"""

import json
from typing import Any

from app.core.errors import ConflictError
from app.services.episode_duration_policy import episode_duration_guidance
from app.services.narrative_spec_service import narrative_spec_from_settings

_STRUCTURE_NAMES = {
    "continuous": "连续故事",
    "independent": "单集独立",
    "unit": "单元故事",
    "hybrid": "单集独立＋长期主线",
}


def confirmed_narrative_spec(settings: dict[str, Any] | None) -> dict[str, Any]:
    spec = narrative_spec_from_settings(settings)
    if spec.get("status") != "confirmed" or not spec.get("structure"):
        raise ConflictError("请先确认剧集结构，再生成分集大纲")
    return spec


def _unit_lines(spec: dict[str, Any]) -> str:
    units = spec.get("units") or []
    if not units:
        return "单元范围资料为空，不能自行虚构单元边界。"
    return "\n".join(
        f"- {unit.get('unit_id')}: {unit.get('title')}，第{unit.get('episode_start')}—"
        f"{unit.get('episode_end')}集，单元关系={unit.get('continuity')}，"
        f"持续事实={json.dumps(unit.get('persistent_facts') or [], ensure_ascii=False)}"
        for unit in units
    )


def structure_prompt(spec: dict[str, Any], *, purpose: str) -> str:
    structure = spec["structure"]
    name = _STRUCTURE_NAMES[structure]
    common = (
        f"已确认剧集结构：{name}（{structure}）；计划{spec['episode_count']}集，"
        f"{episode_duration_guidance(spec['episode_duration'])}角色复用策略："
        f"{spec.get('character_reuse') or '按故事设定执行'}。\n"
        "剧集结构是已确认的运行合同，不能改成另一种结构，不能用风格或模型默认规则覆盖。"
    )
    if structure == "continuous":
        rules = (
            "连续故事规则：建立全剧因果链；每集必须推进主线或人物关系，"
            "承接已发生的事实、角色状态和未解决冲突；结尾可以留下跨集悬念，"
            "但不能只靠悬念代替本集戏剧任务。"
        )
    elif structure == "independent":
        rules = (
            "单集独立规则：每集必须有独立事件、目标、升级和当集收束/回报；"
            "只复用公共世界、固定角色身份和确认过的长期事实，禁止把上一集事件、结果、"
            "临时人物、临时道具或结束状态作为本集开场前提；固定角色复用不等于剧情承接。"
            "每集应能交换播出顺序而不破坏因果理解，synopsis、dramatic_goal和cliffhanger"
            "不得出现‘承接上集/上一集之后/接上回’等依赖语义；不得为了连续追看而强行"
            "添加集尾悬念，cliffhanger可为空。"
        )
    elif structure == "unit":
        rules = (
            "单元故事规则：每集必须归属下方一个且仅一个单元；单元内按该单元的连续性承接，"
            "跨单元只继承列出的持续事实；不能把不同单元拼成一条未授权的全剧事件线。\n"
            f"已确认单元范围：\n{_unit_lines(spec)}"
        )
    else:
        rules = (
            "单集独立＋长期主线规则：每集事件必须在本集形成完整闭环，不能依赖下一集才能理解；"
            "固定角色和世界观复用不等于上一集剧情承接；只允许推进已确认的长期关系、世界事实"
            "或主线节点，禁止把上一集临时案件、客串人物、临时道具或结束状态作为本集开场前提。"
            "cliffhanger只能记录长期主线的自然进展，不能替代本集结局。"
        )
    if purpose == "story_bible":
        if structure in {"independent", "hybrid"}:
            count = int(spec["episode_count"])
            planning = (
                f"故事设定阶段的 event_timeline 必须恰好输出 {count} 项，episode_hint 必须唯一且完整覆盖 "
                f"1 到 {count}；一项只对应一集，不能用少量阶段节点代表多集，不能让多个集号共享同一事件。"
                "每项必须描述该集完整的独立事件、当集目标、升级过程和当集结局/回报；"
                "禁止把同一个故事拆成连续多集，禁止依赖上一集临时状态。"
            )
            if structure == "hybrid":
                planning += "长期主线只能作为每个闭环事件中的附加进展，不能取代当集完整事件。"
            else:
                planning += "任意两项交换播出顺序后仍应可以理解。"
            return f"{common}\n{rules}\n{planning}\n这些是逐集故事选题，不是分集大纲，不需要输出场次或完整剧本。"
        return f"{common}\n{rules}\n故事设定阶段还必须输出可供后续逐集规划的结构框架，不直接生成分集大纲。"
    if purpose == "outline_agent":
        return f"{common}\n{rules}\nAgent只能修改被用户明确要求的结构化内容，并保持上述结构合同。"
    return f"{common}\n{rules}"


def story_bible_strategy_prompt(settings: dict[str, Any] | None, *, frame_only: bool = False) -> str:
    spec = narrative_spec_from_settings(settings)
    explicit_manual_choice = spec.get("source") == "manual" and spec.get("structure")
    if (spec.get("status") != "confirmed" and not explicit_manual_choice) or not spec.get("structure"):
        recommendation = (
            "用户选择自动判断剧集结构。根据创作要求判断连续故事、单集独立、单元故事或独立集加长线，"
            "在故事设定中输出 structure_recommendation：structure为continuous/independent/unit/hybrid，"
            "confidence为high或low，reason说明依据；仅证据明确时标high。单元结构附units，"
            "每项包含unit_id、title、episode_start、episode_end、continuity，完整覆盖计划集数。"
            "信息不足时confidence为low，不擅自默认连续故事，保留可供用户审核的事件。"
        )
        return recommendation + ("本步只输出框架，event_timeline为空；后续按建议结构逐集规划事件。" if frame_only else "event_timeline按建议结构规划；独立/混合须每集一项并完整覆盖集号。")
    prefix = "用户已明确选择该结构，故事设定草稿必须立即遵守；最终确认状态不影响本次生成约束。\n" if explicit_manual_choice and spec.get("status") != "confirmed" else ""
    return prefix + structure_prompt(spec, purpose="story_frame" if frame_only else "story_bible")


def outline_strategy_prompt(settings: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    spec = confirmed_narrative_spec(settings)
    return spec, structure_prompt(spec, purpose="outline")


def outline_agent_strategy_prompt(settings: dict[str, Any] | None) -> str:
    spec = narrative_spec_from_settings(settings)
    if spec.get("status") != "confirmed" or not spec.get("structure"):
        return "当前剧集结构尚未确认；Agent不得擅自把项目当作连续故事修改分集大纲。"
    return structure_prompt(spec, purpose="outline_agent")
