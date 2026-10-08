"""A2 registry and configuration for no-chat business executors."""

# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    AgentSkill,
    AgentSkillVersion,
    BusinessExecutorSetting,
    Provider,
    ProviderModel,
)
from app.services import agent_config_service


@dataclass(frozen=True, slots=True)
class BusinessExecutorDefinition:
    key: str
    name: str
    description: str
    model_type: str | None
    skill_keys: tuple[str, ...]
    tool_keys: tuple[str, ...]
    execution_surfaces: tuple[str, ...]
    billing_behavior: str
    default_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["skill_keys"] = list(self.skill_keys)
        value["tool_keys"] = list(self.tool_keys)
        value["execution_surfaces"] = list(self.execution_surfaces)
        value["chat_entry"] = False
        return value


BUSINESS_EXECUTORS = (
    BusinessExecutorDefinition(
        key="episode_director",
        name="分集导演",
        description="读取定稿剧本、正式资产与视频模型能力，规划和调整可生成的视频片段。",
        model_type="text",
        skill_keys=tuple(item["key"] for item in agent_config_service.DIRECTOR_SKILLS),
        tool_keys=(
            "episode.production.read",
            "episode.production.plan",
            "episode.production.adjust",
            "episode.production.continuity",
        ),
        execution_surfaces=("episode_studio", "agent_tool_call"),
        billing_behavior="text_planning_only",
    ),
    BusinessExecutorDefinition(
        key="asset_prompt_generator",
        name="资产提示词生成器",
        description="把正式资产设定转换成可审阅的一致性图片提示词提案，不直接生成图片。",
        model_type="text",
        skill_keys=("asset.prompt-generator.v1",),
        tool_keys=("asset.prompt.generate",),
        execution_surfaces=("asset_library", "agent_tool_call"),
        billing_behavior="text_generation",
    ),
    BusinessExecutorDefinition(
        key="media_task_orchestrator",
        name="媒体任务编排器",
        description="复用统一预检、权限、费用、幂等和恢复服务提交图片、视频与声音任务。",
        model_type=None,
        skill_keys=("media.task-orchestrator.v1",),
        tool_keys=("episode.production.start", "canvas.media.generate"),
        execution_surfaces=("episode_studio", "asset_library", "infinite_canvas", "agent_tool_call"),
        billing_behavior="delegated_media_cost",
    ),
)
DEFINITIONS_BY_KEY = {item.key: item for item in BUSINESS_EXECUTORS}

EXECUTOR_SKILLS = (
    {
        "key": "asset.prompt-generator.v1",
        "name": "资产一致性提示词",
        "instruction": """根据正式资产 ID 与已确认设定生成逐项视觉身份提示词，只返回入口要求的结构化结果；结果由服务端在版本未变更时自动填入提示词草稿，不提交图片、不采用媒体。

角色提示词只定义稳定身份内容：年龄阶段、脸型与五官特征、自然肤质、体型、发型发色、基础服装阶段、材质磨损、头饰及少量可辨识标志。把固定身份与临时动作、表情、机位、景别、光线和场景分离；不要写坐姿、战斗、奔跑、柔和逆光、浅景深、光晕、背景虚化或海报构图。角色设定图的四分区排版、背景、人物高度上限、边缘安全区、脚底落点、完整入镜和一致性要求由服务端 character-reference-sheet.v2 契约统一追加。

不要把所有人物美化成相似的年轻模特。原设定缺少的重大身份特征不得擅自确定；资料只能支持宽泛描述时保持宽泛，并明确保留原有生活处境和故事阶段。服装、头饰和妆容的剧情阶段必须可追溯，不能把某一镜头的临时状态写成永久身份。

场景提示词只定义固定空间身份：地貌或建筑结构、出入口、标志性陈设、材质、主色、固定光源与空间层次。不写人物、临时剧情动作、机位和浅景深；原资料未确定时不擅自增加季节、天气或昼夜。单一连续空间、稳定透视、深景深、关键结构完整与安全边距由服务端 scene-reference.v1 契约追加。

道具提示词只定义固定实物身份：种类、尺寸比例、轮廓、材质、结构、表面磨损、固定附件和辨识标志。不写手持者、剧情场景、动作或镜头特写。单一道具、完整轮廓、中性背景、落地方式与四周安全边距由服务端 prop-reference.v1 契约追加。

服装提示词只定义单套造型身份：剪裁层次、内外层、材质、颜色、头饰、鞋履、配饰、磨损和剧情阶段。不重新定义角色脸孔、体型和发型，不写坐姿、表演或场景。已绑定角色身份保持、头饰到鞋底完整入镜、人物高度上限和安全边距由服务端 costume-reference.v1 契约追加。

不得返回清单外资产，不漏项、不重复 ID。""",
        "tools": ["asset.prompt.generate"],
        "context": ["project", "asset_ids", "asset_snapshots"],
        "output": {"type": "object", "required": ["assets"]},
        "rules": {"exact_asset_ids": True, "media_submission_forbidden": True},
        "write_policy": "confirmed_write",
    },
    {
        "key": "media.task-orchestrator.v1",
        "name": "媒体任务安全编排",
        "instruction": "只调用既有媒体任务服务；提交前必须通过权限、参数、费用与幂等检查并获得明确确认。",
        "tools": ["episode.production.start", "canvas.media.generate"],
        "context": ["target", "selected_model", "parameters", "pricing_quote", "request_id"],
        "output": {"type": "object", "required": ["job_id", "request_id"]},
        "rules": {"explicit_confirmation": True, "unknown_state_no_retry": True},
        "write_policy": "confirmed_write",
    },
)


def get_definition(key: str) -> BusinessExecutorDefinition:
    definition = DEFINITIONS_BY_KEY.get(key)
    if definition is None:
        raise NotFoundError("业务执行器不存在")
    return definition


async def ensure_executor_skills(session: AsyncSession) -> list[AgentSkill]:
    await agent_config_service.ensure_director_skills(session)
    for spec in EXECUTOR_SKILLS:
        skill = await session.scalar(select(AgentSkill).where(AgentSkill.key == spec["key"]))
        if skill is not None:
            # Versioned catalog installation owns builtin instructions; never reset reviewed content.
            continue
        skill = AgentSkill(
            key=spec["key"],
            name=spec["name"],
            mode="system",
            input_modalities=["text"],
            output_modality="text",
            instruction=spec["instruction"],
            version=1,
            capability_type="structured_action",
            allowed_tools=spec["tools"],
            context_requirements=spec["context"],
            output_schema=spec["output"],
            validation_rules=spec["rules"],
            write_policy=spec["write_policy"],
            is_builtin=True,
            requires_confirmation=True,
            enabled=True,
        )
        session.add(skill)
        await session.flush()
        session.add(
            AgentSkillVersion(
                skill_id=skill.id,
                version=1,
                snapshot=agent_config_service.skill_snapshot(skill),
            )
        )
    await session.flush()
    keys = [key for definition in BUSINESS_EXECUTORS for key in definition.skill_keys]
    return list(
        (
            await session.scalars(
                select(AgentSkill).where(AgentSkill.key.in_(keys)).order_by(AgentSkill.key)
            )
        ).all()
    )


async def ensure_executor_settings(session: AsyncSession) -> list[BusinessExecutorSetting]:
    skills = await ensure_executor_skills(session)
    by_key = {skill.key: skill for skill in skills}
    existing = {
        item.key: item
        for item in (
            await session.scalars(select(BusinessExecutorSetting))
        ).all()
    }
    for definition in BUSINESS_EXECUTORS:
        selections = [
            {"skill_id": by_key[key].id, "version": by_key[key].version}
            for key in definition.skill_keys
        ]
        if definition.key in existing:
            # Catalog upgrades advance reviewed bindings; initialization preserves user pins.
            continue
        setting = BusinessExecutorSetting(
            key=definition.key,
            enabled=definition.default_enabled,
            model_id=None,
            skill_versions=selections,
            approval_policy="explicit_confirmation",
            parameters={},
            revision=0,
        )
        session.add(setting)
        await session.flush()
        existing[definition.key] = setting
    return [existing[item.key] for item in BUSINESS_EXECUTORS]


async def _model_status(
    session: AsyncSession, definition: BusinessExecutorDefinition, model_id: int | None
) -> tuple[dict[str, Any] | None, str | None]:
    if model_id is None:
        if definition.model_type is None:
            return None, None
        return None, "尚未选择执行模型"
    pair = (
        await session.execute(
            select(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(ProviderModel.id == model_id)
        )
    ).one_or_none()
    if pair is None:
        return None, "执行模型不存在"
    model, provider = pair
    value = {
        "id": model.id,
        "provider_id": provider.id,
        "provider_name": provider.name,
        "model_id": model.model_id,
        "name": model.name,
        "model_type": model.model_type,
        "enabled": bool(model.enabled and provider.enabled),
    }
    if model.model_type != definition.model_type:
        return value, f"执行器需要 {definition.model_type} 模型"
    if not model.enabled or not provider.enabled:
        return value, "所选模型或渠道已停用"
    return value, None


async def _skill_details(
    session: AsyncSession,
    definition: BusinessExecutorDefinition,
    selections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    skills = list(
        (
            await session.scalars(
                select(AgentSkill).where(AgentSkill.key.in_(definition.skill_keys))
            )
        ).all()
    )
    by_id = {skill.id: skill for skill in skills}
    selected = {int(item.get("skill_id", 0)): int(item.get("version", 0)) for item in selections}
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for key in definition.skill_keys:
        skill = next((item for item in skills if item.key == key), None)
        if skill is None:
            errors.append(f"缺少内置 Skill：{key}")
            continue
        versions = list(
            (
                await session.scalars(
                    select(AgentSkillVersion)
                    .where(AgentSkillVersion.skill_id == skill.id)
                    .order_by(AgentSkillVersion.version.desc())
                )
            ).all()
        )
        chosen = selected.get(skill.id, skill.version)
        selected_version = next((item for item in versions if item.version == chosen), None)
        if selected_version is None:
            errors.append(f"{skill.name} 的 V{chosen} 不存在")
        if not skill.enabled:
            errors.append(f"{skill.name} 已停用")
        result.append(
            {
                "skill_id": skill.id,
                "key": skill.key,
                "name": skill.name,
                "current_version": skill.version,
                "selected_version": chosen,
                "available_versions": [item.version for item in versions],
                "enabled": skill.enabled,
                # Pydantic omits this internal execution detail from settings responses.
                # Runtime services still receive the immutable selected instructions.
                "snapshot": dict(selected_version.snapshot or {}) if selected_version else {},
            }
        )
    unknown = [skill_id for skill_id in selected if skill_id not in by_id]
    if unknown:
        errors.append("配置包含不属于该执行器的 Skill")
    return result, errors


async def to_output(
    session: AsyncSession, setting: BusinessExecutorSetting
) -> dict[str, Any]:
    definition = get_definition(setting.key)
    model, model_error = await _model_status(session, definition, setting.model_id)
    skills, skill_errors = await _skill_details(
        session, definition, list(setting.skill_versions or [])
    )
    issues = [item for item in [model_error, *skill_errors] if item]
    return {
        **definition.to_dict(),
        "enabled": setting.enabled,
        "model_id": setting.model_id,
        "model": model,
        "skills": skills,
        "approval_policy": setting.approval_policy,
        "parameters": dict(setting.parameters or {}),
        "revision": setting.revision,
        "ready": setting.enabled and not issues,
        "issues": issues,
    }


async def list_executors(session: AsyncSession) -> list[dict[str, Any]]:
    settings = await ensure_executor_settings(session)
    return [await to_output(session, item) for item in settings]


async def update_executor(
    session: AsyncSession,
    key: str,
    *,
    enabled: bool | None,
    model_id: int | None,
    model_id_set: bool,
    skill_versions: list[dict[str, int]] | None,
    approval_policy: str | None,
    parameters: dict[str, Any] | None,
    expected_revision: int,
) -> dict[str, Any]:
    definition = get_definition(key)
    await ensure_executor_settings(session)
    setting = await session.scalar(
        select(BusinessExecutorSetting).where(BusinessExecutorSetting.key == key)
    )
    if setting is None:
        raise NotFoundError("业务执行器配置不存在")
    if setting.revision != expected_revision:
        raise ConflictError("业务执行器配置已被修改，请刷新后重试")
    if enabled is not None:
        setting.enabled = enabled
    if model_id_set:
        setting.model_id = model_id
    if skill_versions is not None:
        skill_ids = [item["skill_id"] for item in skill_versions]
        if len(skill_ids) != len(set(skill_ids)):
            raise ConflictError("业务执行器不能重复选择同一个 Skill")
        _details, errors = await _skill_details(session, definition, skill_versions)
        if errors:
            raise ConflictError(errors[0])
        setting.skill_versions = skill_versions
    if approval_policy is not None:
        setting.approval_policy = approval_policy
    if parameters is not None:
        setting.parameters = parameters
    if setting.approval_policy != "explicit_confirmation":
        raise ConflictError("当前只允许明确确认策略，不能绕过审批")
    if setting.model_id is not None:
        _model, error = await _model_status(session, definition, setting.model_id)
        if error:
            raise ConflictError(error)
    setting.revision += 1
    await session.flush()
    return await to_output(session, setting)


async def resolve_execution(
    session: AsyncSession,
    key: str,
    *,
    requested_model_id: int | None = None,
) -> dict[str, Any]:
    await ensure_executor_settings(session)
    setting = await session.scalar(
        select(BusinessExecutorSetting).where(BusinessExecutorSetting.key == key)
    )
    if setting is None:
        raise NotFoundError("业务执行器配置不存在")
    if not setting.enabled:
        raise ConflictError(f"{get_definition(key).name}已停用，请在 AI 控制中心启用")
    definition = get_definition(key)
    effective_model_id = requested_model_id or setting.model_id
    model, model_error = await _model_status(session, definition, effective_model_id)
    if model_error:
        raise ConflictError(model_error)
    skills, errors = await _skill_details(session, definition, setting.skill_versions or [])
    if errors:
        raise ConflictError(errors[0])
    return {
        "executor_key": key,
        "executor_revision": setting.revision,
        "approval_policy": setting.approval_policy,
        "model": model,
        "skills": skills,
        "tool_keys": list(definition.tool_keys),
        "billing_behavior": definition.billing_behavior,
        "parameters": dict(setting.parameters or {}),
    }
