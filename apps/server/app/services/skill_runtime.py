"""Entry contracts for existing Skills; never creates bindings or model calls."""

# ruff: noqa: RUF001

CREATION_SKILLS = {
    ("outline", "creative_direction"): "outline.direction",
    ("outline", "story_bible"): "outline.story_bible",
    ("outline", "episode_outline"): "outline.rewrite",
    ("outline", "asset_breakdown"): "outline.asset_extract",
    ("script", "episode_script"): "script.from_brief",
    ("script", "episode_script_generation"): "script.from_brief",
    ("script", "episode_script_optimization"): "script.rewrite",
    ("script", "script_continuity_check"): "script.rewrite",
    ("script", "script_continuity_repair"): "script.rewrite",
    ("script", "script_study"): "script.rewrite",
    ("script", "episode_scene_shot_breakdown"): "script.rewrite",
    ("script", "scene_shot_draft"): "script.rewrite",
}
BUILTIN_CREATION_KEYS = frozenset(CREATION_SKILLS.values())


def creation_skills(skills, agent_key: str, surface: str):
    """Filter conflicting builtins, retaining explicitly bound custom text Skills.

    Unconfigured tasks retain the existing entry prompt, not a silently installed
    Skill. Unknown surfaces do not inherit unrelated builtin instructions.
    """
    expected = CREATION_SKILLS.get((agent_key, surface))
    return [
        skill for skill in skills
        if skill.enabled and skill.mode == agent_key and skill.output_modality == "text"
        and (skill.key == expected or skill.key not in BUILTIN_CREATION_KEYS)
    ]


def text_optimization_skill(skills):
    candidates = [
        skill for skill in skills
        if skill.enabled and skill.mode == "canvas" and skill.output_modality == "text"
        and skill.key != "canvas.control"
    ]
    return next(
        (skill for skill in candidates if skill.key == "canvas.text.generate"),
        next(iter(candidates), None),
    )


def creation_contract(surface: str) -> str:
    return (
        f"本次任务入口：{surface}。只处理下方入口明确指定的任务和提供的上下文。"
        "技能是创作指导，不改变本次输出字段、工具权限或确认要求。"
        "分析、研读、提取和拆解不得擅自改写原文；新创作与改写以本轮授权为限。"
        "仅核验实际提供的内容，不声称已阅读未提供的全项目资料。"
    )


DIRECTOR_CONTRACT = (
    "以上六项 Skill 是本次单次规划请求的职责分工，不是六个独立调用。"
    "只返回固定示例的顶层 shots、segments、continuity_issues；"
    "片段提示词和参数写入 segments，不另返顶层 prompt、parameters 或资产绑定数组。"
    "只规划已有 shot_id，资产绑定由服务端处理。"
    "仅审查输入计划；素材 ID 不代表已看过图片，不得宣称已验证最终视频、音频或口型。"
)
