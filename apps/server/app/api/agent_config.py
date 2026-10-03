"""系统设置的 Skill 与视觉风格接口。"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas.agent_config import (
    AgentDefinitionOut,
    AgentSkillCreate,
    AgentSkillOut,
    AgentSkillUpdate,
    AgentSkillVersionOut,
    AgentToolOut,
    BusinessExecutorOut,
    BusinessExecutorUpdate,
    StylePresetCreate,
    StylePresetOut,
    StylePresetUpdate,
)
from app.services import agent_config_service, agent_registry, business_executor_service

router = APIRouter(prefix="/agent-config", tags=["agent-config"])


class StyleImageGenerate(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=4000)


@router.post("/styles/generate-image", status_code=status.HTTP_201_CREATED)
async def generate_style_image(payload: StyleImageGenerate, session: SessionDep, admin: AdminUser):
    from app.core.errors import ConflictError
    from app.schemas.job import JobOut
    from app.services import job_service, provider_service

    if not payload.prompt.strip():
        raise ConflictError("请先填写风格提示词")
    model, _ = await provider_service.resolve_default_model(session, "image")
    job = await job_service.create_image_job(
        session, admin.id, project_id=None, asset_id=0, provider_model_id=model.id,
        prompt=payload.prompt.strip(), negative_prompt=payload.negative_prompt,
        reference_media_ids=[], parameters={"n": 1},
    )
    job.target_type = "style_image"
    job.target_id = None
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/definitions", response_model=list[AgentDefinitionOut])
async def list_agent_definitions(_user: CurrentUser) -> list[AgentDefinitionOut]:
    return [AgentDefinitionOut.model_validate(item) for item in agent_registry.list_agent_definitions()]


@router.get("/skills", response_model=list[AgentSkillOut])
async def list_skills(session: SessionDep, _user: CurrentUser, mode: str | None = None) -> list[AgentSkillOut]:
    from app.core.workspace_context import isolation_enabled
    return [AgentSkillOut.model_validate(item).model_copy(update={"editable": not (isolation_enabled() and item.is_builtin)})
            for item in await agent_config_service.list_skills(session, mode)]


@router.get("/tools", response_model=list[AgentToolOut])
async def list_agent_tools(_user: CurrentUser) -> list[AgentToolOut]:
    return [AgentToolOut.model_validate(item) for item in agent_config_service.AGENT_TOOL_CATALOG]


@router.get("/executors", response_model=list[BusinessExecutorOut])
async def list_business_executors(
    session: SessionDep, _user: CurrentUser
) -> list[BusinessExecutorOut]:
    items = await business_executor_service.list_executors(session)
    await session.commit()
    return [BusinessExecutorOut.model_validate(item) for item in items]


@router.patch("/executors/{executor_key}", response_model=BusinessExecutorOut)
async def update_business_executor(
    executor_key: str,
    payload: BusinessExecutorUpdate,
    session: SessionDep,
    _admin: AdminUser,
) -> BusinessExecutorOut:
    item = await business_executor_service.update_executor(
        session,
        executor_key,
        enabled=payload.enabled,
        model_id=payload.model_id,
        model_id_set="model_id" in payload.model_fields_set,
        skill_versions=(
            [selection.model_dump() for selection in payload.skill_versions]
            if payload.skill_versions is not None
            else None
        ),
        approval_policy=payload.approval_policy,
        parameters=payload.parameters,
        expected_revision=payload.expected_revision,
    )
    await session.commit()
    return BusinessExecutorOut.model_validate(item)


@router.get("/skills/{skill_id}/versions", response_model=list[AgentSkillVersionOut])
async def list_skill_versions(skill_id: int, session: SessionDep, _user: CurrentUser) -> list[AgentSkillVersionOut]:
    return [AgentSkillVersionOut.model_validate(item) for item in await agent_config_service.list_skill_versions(session, skill_id)]


@router.post("/skills", response_model=AgentSkillOut, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: AgentSkillCreate, session: SessionDep, _admin: AdminUser) -> AgentSkillOut:
    skill = await agent_config_service.create_skill(session, payload.model_dump())
    await session.commit()
    return AgentSkillOut.model_validate(skill)


@router.patch("/skills/{skill_id}", response_model=AgentSkillOut)
async def update_skill(skill_id: int, payload: AgentSkillUpdate, session: SessionDep, _admin: AdminUser) -> AgentSkillOut:
    skill = await agent_config_service.update_skill(session, await agent_config_service.get_skill(session, skill_id), payload.model_dump(exclude_unset=True))
    await session.commit()
    return AgentSkillOut.model_validate(skill)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: int, session: SessionDep, _admin: AdminUser) -> None:
    await agent_config_service.delete_skill(session, await agent_config_service.get_skill(session, skill_id))
    await session.commit()


@router.get("/styles", response_model=list[StylePresetOut])
async def list_styles(session: SessionDep, _user: CurrentUser) -> list[StylePresetOut]:
    return [StylePresetOut.model_validate(item) for item in await agent_config_service.list_styles(session)]


@router.post("/styles", response_model=StylePresetOut, status_code=status.HTTP_201_CREATED)
async def create_style(payload: StylePresetCreate, session: SessionDep, admin: AdminUser) -> StylePresetOut:
    style = await agent_config_service.create_style(session, payload.model_dump(), owner_id=admin.id)
    await session.commit()
    return StylePresetOut.model_validate(style)


@router.patch("/styles/{style_id}", response_model=StylePresetOut)
async def update_style(style_id: int, payload: StylePresetUpdate, session: SessionDep, admin: AdminUser) -> StylePresetOut:
    style = await agent_config_service.update_style(
        session,
        await agent_config_service.get_style(session, style_id),
        payload.model_dump(exclude_unset=True),
        owner_id=admin.id,
    )
    await session.commit()
    return StylePresetOut.model_validate(style)


@router.delete("/styles/{style_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_style(style_id: int, session: SessionDep, _admin: AdminUser) -> None:
    await agent_config_service.delete_style(session, await agent_config_service.get_style(session, style_id))
    await session.commit()
