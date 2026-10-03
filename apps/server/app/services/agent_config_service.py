"""系统设置中的 Skill 与视觉风格管理。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import AgentSkill, AgentSkillVersion, AISettings, MediaFile, StylePreset
from app.models.agent_config import StyleCategory
from app.services.team_access import same_team

AGENT_TOOL_CATALOG = (
    {"key": "source_script.analyze", "name": "分析上传剧本", "agent": "outline", "description": "分析原稿结构并生成可审阅的标准化与分集建议。", "write_policy": "proposal"},
    {"key": "story.read", "name": "读取故事上下文", "agent": "outline", "description": "读取项目设定、剧本和已有分集结构。", "write_policy": "read_only"},
    {"key": "story.direction.propose", "name": "提议故事方向", "agent": "outline", "description": "根据创意与可编辑理解生成三个可审阅的差异化方向。", "write_policy": "proposal"},
    {"key": "story.outline.generate", "name": "生成故事大纲", "agent": "outline", "description": "基于确认设定生成结构化故事与分集大纲提案。", "write_policy": "proposal"},
    {"key": "story_bible.propose", "name": "提议故事圣经", "agent": "outline", "description": "生成或修改故事设定提案，确认后写入。", "write_policy": "proposal"},
    {"key": "episode_outline.propose", "name": "提议分集规划", "agent": "outline", "description": "拆解集数、节奏和悬念，确认后写入。", "write_policy": "proposal"},
    {"key": "asset_candidates.propose", "name": "提取资产候选", "agent": "outline", "description": "从剧本提取角色、场景和道具候选。", "write_policy": "proposal"},
    {"key": "script.read", "name": "读取剧本上下文", "agent": "script", "description": "读取创意、参考材料和已有分集。", "write_policy": "read_only"},
    {"key": "episode_script.generate", "name": "生成分集剧本", "agent": "script", "description": "生成可审阅的分集剧本草案。", "write_policy": "proposal"},
    {"key": "episode_script.rewrite", "name": "改写分集剧本", "agent": "script", "description": "按用户要求改写已选内容。", "write_policy": "proposal"},
    {"key": "scene_shot.propose", "name": "拆解场景与分镜", "agent": "script", "description": "将确认剧本转换为场景和分镜提案。", "write_policy": "proposal"},
    {"key": "canvas.read", "name": "读取画布", "agent": "canvas", "description": "读取当前画布节点、连线和选区。", "write_policy": "read_only"},
    {"key": "canvas.node.propose", "name": "提议节点操作", "agent": "canvas", "description": "提议创建、修改、连接和排列节点。", "write_policy": "proposal"},
    {"key": "canvas.group.propose", "name": "提议分组与排列", "agent": "canvas", "description": "提议分组、解组及宫格/横向/纵向排列。", "write_policy": "proposal"},
    {"key": "canvas.media.generate", "name": "生成画布媒体", "agent": "canvas", "description": "确认模型、参数和费用后提交媒体任务。", "write_policy": "confirmed_write"},
    {"key": "canvas.media.bind", "name": "绑定画布媒体", "agent": "canvas", "description": "经确认后将媒体版本绑定到节点或实体。", "write_policy": "confirmed_write"},
    {"key": "market.search", "name": "真实市场检索", "agent": "market", "description": "通过已配置搜索能力获取可追溯来源。", "write_policy": "read_only"},
    {"key": "market.report", "name": "生成市场报告", "agent": "market", "description": "基于来源生成趋势、机会和风险报告。", "write_policy": "read_only"},
    {"key": "market.idea.propose", "name": "提议采用选题", "agent": "market", "description": "把选中的市场创意作为项目创建提案。", "write_policy": "proposal"},
    {"key": "episode.production.read", "name": "读取分集生产上下文", "agent": "system", "description": "读取定稿剧本、分镜、资产引用和模型能力快照。", "write_policy": "read_only"},
    {"key": "episode.production.plan", "name": "规划视频片段", "agent": "system", "description": "形成可审阅的摄影分镜、片段组合和供应商 Prompt。", "write_policy": "proposal"},
    {"key": "episode.production.adjust", "name": "调整片段计划", "agent": "system", "description": "拆分、合并或重新编排片段，并创建新的计划版本。", "write_policy": "proposal"},
    {"key": "episode.production.continuity", "name": "检查制作连续性", "agent": "system", "description": "检查资产、场景状态、动作和片段首尾连续性。", "write_policy": "read_only"},
    {"key": "asset.prompt.generate", "name": "优化资产提示词", "agent": "system", "description": "为正式资产生成一致性提示词并自动填入；不提交图片。", "write_policy": "confirmed_write"},
    {"key": "episode.production.start", "name": "发起片段生产", "agent": "system", "description": "复用片段预检、费用确认、幂等和恢复服务提交正式媒体任务。", "write_policy": "confirmed_write"},
)
AGENT_TOOLS_BY_KEY = {item["key"]: item for item in AGENT_TOOL_CATALOG}
SKILL_BEHAVIOR_FIELDS = {
    "mode", "input_modalities", "output_modality", "instruction", "capability_type",
    "allowed_tools", "context_requirements", "output_schema", "validation_rules",
    "write_policy", "requires_confirmation",
}


def _skill_snapshot(skill: AgentSkill) -> dict:
    return {
        "key": skill.key, "name": skill.name, "mode": skill.mode,
        "input_modalities": list(skill.input_modalities), "output_modality": skill.output_modality,
        "instruction": skill.instruction, "capability_type": skill.capability_type,
        "allowed_tools": list(skill.allowed_tools), "context_requirements": list(skill.context_requirements),
        "output_schema": dict(skill.output_schema), "validation_rules": dict(skill.validation_rules),
        "write_policy": skill.write_policy, "requires_confirmation": skill.requires_confirmation,
    }


def skill_snapshot(skill: AgentSkill) -> dict:
    """Public immutable snapshot builder shared by fixed business executors."""
    return _skill_snapshot(skill)


def _validate_skill_data(data: dict, current: AgentSkill | None = None) -> dict:
    merged = _skill_snapshot(current) if current is not None else {}
    merged.update(data)
    mode = merged.get("mode")
    capability_type = merged.get("capability_type", "text_assist")
    allowed_tools = merged.get("allowed_tools") or []
    unknown = [key for key in allowed_tools if key not in AGENT_TOOLS_BY_KEY]
    if unknown:
        raise ConflictError(f"Skill 包含未知工具：{', '.join(unknown)}")
    wrong_agent = [key for key in allowed_tools if AGENT_TOOLS_BY_KEY[key]["agent"] != mode]
    if wrong_agent:
        raise ConflictError(f"Skill 工具与适用 Agent 不一致：{', '.join(wrong_agent)}")
    if capability_type == "text_assist" and allowed_tools:
        raise ConflictError("文本辅助 Skill 不能声明系统工具；请选择结构化操作、媒体生成或检索能力")
    if merged.get("write_policy") == "confirmed_write":
        data["requires_confirmation"] = True
    return data


async def list_skills(session: AsyncSession, mode: str | None = None) -> list[AgentSkill]:
    statement = select(AgentSkill)
    if mode is not None:
        statement = statement.where(AgentSkill.mode == mode)
    return list((await session.execute(statement.order_by(AgentSkill.mode, AgentSkill.key))).scalars())


async def get_skill(session: AsyncSession, skill_id: int) -> AgentSkill:
    skill = await session.get(AgentSkill, skill_id)
    if skill is None:
        raise NotFoundError("Skill 不存在")
    return skill


async def create_skill(session: AsyncSession, data: dict) -> AgentSkill:
    if await session.scalar(select(AgentSkill.id).where(AgentSkill.key == data["key"])):
        raise ConflictError("Skill 标识已存在")
    data = _validate_skill_data(data)
    skill = AgentSkill(**data, version=1, is_builtin=False)
    session.add(skill)
    await session.flush()
    session.add(AgentSkillVersion(skill_id=skill.id, version=1, snapshot=_skill_snapshot(skill)))
    await session.flush()
    return skill


async def update_skill(session: AsyncSession, skill: AgentSkill, data: dict) -> AgentSkill:
    from app.core.workspace_context import isolation_enabled
    if isolation_enabled() and skill.is_builtin:
        raise ConflictError("内置 Skill 为只读模板，请创建自定义版本")
    key = data.get("key")
    if key is not None and key != skill.key and await session.scalar(select(AgentSkill.id).where(AgentSkill.key == key)):
        raise ConflictError("Skill 标识已存在")
    data = _validate_skill_data(data, skill)
    changed_behavior = any(field in data and getattr(skill, field) != value for field, value in data.items() if field in SKILL_BEHAVIOR_FIELDS)
    if changed_behavior:
        skill.version += 1
    for field, value in data.items():
        setattr(skill, field, value)
    await session.flush()
    if changed_behavior:
        session.add(AgentSkillVersion(skill_id=skill.id, version=skill.version, snapshot=_skill_snapshot(skill)))
        await session.flush()
    return skill


async def delete_skill(session: AsyncSession, skill: AgentSkill) -> None:
    if skill.is_builtin:
        raise ConflictError("内置 Skill 不能删除，可以停用或创建自定义版本")
    settings_rows = list((await session.execute(select(AISettings))).scalars())
    for settings in settings_rows:
        bindings = settings.agent_skill_bindings or {}
        if skill.id in {item for values in bindings.values() for item in values} or skill.id in {
            settings.outline_agent_skill_id, settings.script_agent_skill_id,
        }:
            raise ConflictError("Skill 正在被 Agent 使用，请先解除绑定")
    await session.delete(skill)
    await session.flush()


async def list_skill_versions(session: AsyncSession, skill_id: int) -> list[AgentSkillVersion]:
    await get_skill(session, skill_id)
    return list((await session.execute(
        select(AgentSkillVersion).where(AgentSkillVersion.skill_id == skill_id).order_by(AgentSkillVersion.version.desc())
    )).scalars())


DIRECTOR_SKILLS = (
    {
        "key": "episode.script-analysis.v1", "name": "分集剧本分析", "tool": "episode.production.read",
        "instruction": "只从定稿剧本与结构化上下文提取剧情节拍、场景、人物、对白和动作，不补写不存在的资产。",
        "context": ["finalized_script", "target_duration", "source_script_revision"],
        "output": {"type": "object", "required": ["shots", "segments", "continuity_issues"]},
        "rules": {"finalized_revision_required": True, "target_duration_required": True},
    },
    {
        "key": "episode.shot-planning.v1", "name": "摄影分镜规划", "tool": "episode.production.plan",
        "instruction": "为每个既有分镜规划时长、景别、机位、焦段语义、运镜、动作、对白和声音；分镜是摄影语言，不等于一次视频调用。",
        "context": ["script", "scenes", "shots"],
        "output": {"type": "array", "items": "director_shot.v1"},
        "rules": {"positive_duration": True, "cover_each_shot_once": True},
    },
    {
        "key": "episode.segment-grouping.v1", "name": "视频片段组合", "tool": "episode.production.plan",
        "instruction": "按视频模型的离散时长、多分镜上限和场景连续性，把连续分镜组合成一次模型调用对应的视频片段。",
        "context": ["shot_plan", "video_model_capabilities"],
        "output": {"type": "array", "items": "director_segment.v1"},
        "rules": {"legal_generation_duration": True, "contiguous_shots": True},
    },
    {
        "key": "episode.asset-binding.v1", "name": "制作资产绑定", "tool": "episode.production.read",
        "instruction": "只使用服务端提供的 AssetUsage、AssetVersion 与 Media ID；无法唯一解析时标记待绑定，禁止按名称猜测或选择第一张图。",
        "context": ["asset_usage_snapshot"],
        "output": {"type": "array", "items": "asset_binding.v1"},
        "rules": {"exact_ids_only": True, "unresolved_must_be_reported": True},
    },
    {
        "key": "episode.prompt-compiler.v1", "name": "片段 Prompt 编译", "tool": "episode.production.plan",
        "instruction": "将通用片段脚本编译为所选视频模型可接受的 Prompt 与参数；不调用媒体生成接口。",
        "context": ["segment_plan", "video_model_capabilities"],
        "output": {"type": "object", "required": ["prompt", "parameters"]},
        "rules": {"unsupported_parameters_forbidden": True, "media_submission_forbidden": True},
    },
    {
        "key": "episode.continuity-check.v1", "name": "片段连续性检查", "tool": "episode.production.continuity",
        "instruction": "检查人物造型、场景状态、时间、方向、动作承接和片段首尾连续性，并输出可审阅问题。",
        "context": ["segments", "asset_bindings"],
        "output": {"type": "array", "items": "continuity_issue.v1"},
        "rules": {"issues_are_advisory_unless_blocking": True},
    },
)


async def ensure_director_skills(session: AsyncSession) -> list[AgentSkill]:
    """Idempotently install the fixed, versioned E3 director skill bundle."""
    from app.services.reviewed_catalog_service import install_skills
    await install_skills(session)
    result: list[AgentSkill] = []
    for spec in DIRECTOR_SKILLS:
        skill = await session.scalar(select(AgentSkill).where(AgentSkill.key == spec["key"]))
        if skill is None:
            skill = AgentSkill(
                key=spec["key"], name=spec["name"], mode="system",
                input_modalities=["text"], output_modality="text",
                instruction=spec["instruction"], version=1,
                capability_type="structured_action", allowed_tools=[spec["tool"]],
                context_requirements=spec["context"], output_schema=spec["output"],
                validation_rules=spec["rules"], write_policy="proposal",
                is_builtin=True, requires_confirmation=True, enabled=True,
            )
            session.add(skill)
            await session.flush()
            session.add(AgentSkillVersion(skill_id=skill.id, version=1, snapshot=_skill_snapshot(skill)))
            await session.flush()
        result.append(skill)
    return result


DEFAULT_STYLES = [
    ("复古科幻原子朋克", "真人", "复古未来主义，暖沙色与青绿色，胶片颗粒，机械细节。"),
    ("宫廷权谋冷峻", "真人", "深色宫廷，低调光，金色细节，克制构图与人物对峙。"),
    ("悬疑冷调", "真人", "冷青色调，高反差，雨夜街巷，阴影与紧张气氛。"),
    ("古偶唯美柔光", "真人", "古风服饰，柔和逆光，浅粉与暖白，唯美留白。"),
    ("青春胶片", "真人", "青春校园，自然光，清新绿色，细腻胶片质感。"),
    ("都市生活写实", "真人", "日常生活，真实街景，自然肤色，温暖纪实摄影。"),
    ("东方水墨", "2D", "水墨山水，黑白层次，纸张肌理，简洁线条与留白。"),
    ("清新动画", "2D", "清晰二维线稿，明亮天空，干净色块，轻盈动画质感。"),
    ("温暖绘本", "2D", "温暖手绘，纸张纹理，柔和色彩，童话叙事。"),
    ("黏土定格", "3D", "手工黏土，圆润造型，微缩布景，柔软材质。"),
    ("奇幻冒险", "3D", "立体幻想世界，层次丰富，宏大场景，梦幻光线。"),
    ("未来科幻", "3D", "未来都市，几何建筑，冷色霓虹，金属与玻璃材质。"),
]


async def ensure_default_styles(session: AsyncSession) -> None:
    from app.services.reviewed_catalog_service import install_styles
    await install_styles(session)


async def list_styles(session: AsyncSession) -> list[StylePreset]:
    return list((await session.execute(select(StylePreset).order_by(StylePreset.name))).scalars())


async def get_style(session: AsyncSession, style_id: int) -> StylePreset:
    style = await session.get(StylePreset, style_id)
    if style is None:
        raise NotFoundError("视觉风格不存在")
    return style


async def _validate_style_media(session: AsyncSession, data: dict, owner_id: int) -> None:
    if "category_ids" in data and data["category_ids"] is not None:
        ids = list(dict.fromkeys(data["category_ids"]))
        valid = set((await session.scalars(select(StyleCategory.id))).all())
        if any(value not in valid for value in ids):
            raise NotFoundError("风格分类已删除，请重新选择")
        data["category_ids"] = ids
        data["category_id"] = ids[0] if ids else None
    else:
        data.pop("category_ids", None)
        if "category_id" in data:
            data["category_ids"] = [data["category_id"]] if data["category_id"] else []
    if data.get("category_id") is not None and await session.get(StyleCategory, data["category_id"]) is None:
        raise NotFoundError("风格分类已删除，请重新选择")
    for field in ("preview_media_id", "reference_media_id"):
        if field not in data or data[field] is None:
            continue
        media = await session.get(MediaFile, data[field])
        if media is None or not await same_team(session, media.owner_id, owner_id):
            raise NotFoundError("风格图片不存在或当前团队无权访问")
        if media.kind != "image":
            raise ConflictError("风格封面与参考素材只能使用图片")
        if media.project_id is not None:
            raise ConflictError("风格图片必须来自全局素材库，不能绑定项目临时媒体")


async def create_style(session: AsyncSession, data: dict, *, owner_id: int) -> StylePreset:
    if await session.scalar(select(StylePreset.id).where(StylePreset.name == data["name"])):
        raise ConflictError("视觉风格名称已存在")
    await _validate_style_media(session, data, owner_id)
    style = StylePreset(**data)
    session.add(style)
    await session.flush()
    return style


async def update_style(session: AsyncSession, style: StylePreset, data: dict, *, owner_id: int) -> StylePreset:
    name = data.get("name")
    if name is not None and name != style.name and await session.scalar(select(StylePreset.id).where(StylePreset.name == name)):
        raise ConflictError("视觉风格名称已存在")
    await _validate_style_media(session, data, owner_id)
    for field, value in data.items():
        setattr(style, field, value)
    await session.flush()
    return style


async def delete_style(session: AsyncSession, style: StylePreset) -> None:
    await session.delete(style)
    await session.flush()
