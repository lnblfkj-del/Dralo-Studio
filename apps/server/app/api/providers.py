"""模型渠道与模型定义接口。"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas.provider import (
    AISettingsOut,
    AISettingsUpdate,
    ProviderCreate,
    ProviderDiscoveryOut,
    ProviderModelCreate,
    ProviderModelOut,
    ProviderModelTestOut,
    ProviderModelTestRequest,
    ProviderModelUpdate,
    ProviderOut,
    ProviderPresetOut,
    ProviderUpdate,
)
from app.services import provider_service

router = APIRouter(prefix="/providers", tags=["providers"])


class PriceEstimateRequest(BaseModel):
    prompt: str = Field(default="", max_length=100000)
    parameters: dict = Field(default_factory=dict)


class VideoPromptPreviewRequest(BaseModel):
    structured_script: dict = Field(default_factory=dict)
    input_contract: dict = Field(default_factory=dict)
    project_style: str = Field(default="", max_length=4000)
    voice_guidance: str = Field(default="", max_length=4000)
    authored_prompt: str | None = Field(default=None, max_length=40000)
    authoring_source_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


@router.post("/{provider_id}/models/{model_id}/estimate")
async def estimate_model_price(provider_id: int, model_id: int, payload: PriceEstimateRequest,
                               session: SessionDep, _user: CurrentUser):
    from app.services.pricing_service import estimate
    model = await provider_service.get_model(session, provider_id, model_id)
    return estimate(model, payload.prompt, payload.parameters)


@router.get("/protocols")
async def list_protocols(_user: CurrentUser):
    from app.providers.protocols import PROTOCOL_CATALOG
    return PROTOCOL_CATALOG


@router.get("/presets", response_model=list[ProviderPresetOut])
async def list_provider_presets(_user: CurrentUser) -> list[ProviderPresetOut]:
    return [ProviderPresetOut.model_validate(item) for item in provider_service.PROVIDER_PRESETS]


@router.get("/ai-settings", response_model=AISettingsOut)
async def get_ai_settings(
    session: SessionDep, _user: CurrentUser
) -> AISettingsOut:
    return AISettingsOut.model_validate(
        await provider_service.to_ai_settings_out(session)
    )


@router.patch("/ai-settings", response_model=AISettingsOut)
async def update_ai_settings(
    payload: AISettingsUpdate, session: SessionDep, _admin: AdminUser
) -> AISettingsOut:
    await provider_service.update_ai_settings(
        session, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return AISettingsOut.model_validate(
        await provider_service.to_ai_settings_out(session)
    )


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    session: SessionDep, _user: CurrentUser
) -> list[ProviderOut]:
    providers = await provider_service.list_providers(session)
    return [ProviderOut.model_validate(provider_service.to_provider_out(item)) for item in providers]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreate, session: SessionDep, admin: AdminUser
) -> ProviderOut:
    data = payload.model_dump(mode="json")
    provider = await provider_service.create_provider(session, admin.id, data)
    await session.commit()
    return ProviderOut.model_validate(provider_service.to_provider_out(provider))


@router.get("/adapter-coverage")
async def adapter_coverage(session: SessionDep, _user: CurrentUser):
    from app.providers.coverage import model_coverage
    providers = await provider_service.list_providers(session)
    return {"items": [model_coverage(provider, model) for provider in providers for model in provider.models]}


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: int, session: SessionDep, _user: CurrentUser
) -> ProviderOut:
    provider = await provider_service.get_provider(session, provider_id)
    return ProviderOut.model_validate(provider_service.to_provider_out(provider))


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: int,
    payload: ProviderUpdate,
    session: SessionDep,
    _admin: AdminUser,
) -> ProviderOut:
    provider = await provider_service.get_provider(session, provider_id)
    updated = await provider_service.update_provider(
        session, provider, payload.model_dump(mode="json", exclude_unset=True)
    )
    await session.commit()
    return ProviderOut.model_validate(provider_service.to_provider_out(updated))


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: int, session: SessionDep, _admin: AdminUser
) -> None:
    provider = await provider_service.get_provider(session, provider_id)
    await provider_service.delete_provider(session, provider)
    await session.commit()


@router.post("/{provider_id}/discover", response_model=ProviderDiscoveryOut)
async def discover_provider_models(
    provider_id: int, session: SessionDep, _admin: AdminUser
) -> ProviderDiscoveryOut:
    provider = await provider_service.get_provider(session, provider_id)
    models, latency_ms = await provider_service.discover_models(provider)
    return ProviderDiscoveryOut(models=models, latency_ms=latency_ms)


@router.post("/{provider_id}/connection-check")
async def check_provider_connection(provider_id: int, session: SessionDep, _admin: AdminUser):
    provider = await provider_service.get_provider(session, provider_id)
    return await provider_service.check_connection(provider)


@router.post(
    "/{provider_id}/models",
    response_model=ProviderModelOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_model(
    provider_id: int,
    payload: ProviderModelCreate,
    session: SessionDep,
    _admin: AdminUser,
) -> ProviderModelOut:
    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.create_model(
        session, provider, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ProviderModelOut.model_validate(model)


@router.patch("/{provider_id}/models/{model_id}", response_model=ProviderModelOut)
async def update_provider_model(
    provider_id: int,
    model_id: int,
    payload: ProviderModelUpdate,
    session: SessionDep,
    _admin: AdminUser,
) -> ProviderModelOut:
    await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    updated = await provider_service.update_model(
        session, model, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ProviderModelOut.model_validate(updated)


@router.post("/{provider_id}/models/{model_id}/test", response_model=ProviderModelTestOut)
async def test_provider_model(
    provider_id: int,
    model_id: int,
    payload: ProviderModelTestRequest,
    session: SessionDep,
    _admin: AdminUser,
) -> ProviderModelTestOut:
    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    result = await provider_service.test_provider_model(
        provider, model, payload.model_dump(mode="json"), owner_id=_admin.id
    )
    return ProviderModelTestOut.model_validate(result)


@router.get("/{provider_id}/models/{model_id}/video-prompt-profile/{input_mode}")
async def get_video_prompt_profile(
    provider_id: int, model_id: int, input_mode: str, session: SessionDep, _user: CurrentUser,
):
    from app.core.errors import ConflictError
    from app.services.video_prompt_compiler import resolve_model_prompt_profile

    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    if model.model_type != "video":
        raise ConflictError("提示词档案只适用于视频模型")
    return resolve_model_prompt_profile(provider, model, input_mode)


@router.post("/{provider_id}/models/{model_id}/video-prompt-preview")
async def preview_video_prompt(
    provider_id: int, model_id: int, payload: VideoPromptPreviewRequest,
    session: SessionDep, _user: CurrentUser,
):
    from app.core.errors import ConflictError
    from app.services.h3_prompt_authoring import (
        accept_h3_authored_result,
        build_h3_authoring_contract,
    )
    from app.services.video_prompt_compiler import (
        compile_model_prompt,
        resolve_model_prompt_profile,
    )

    provider = await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    if model.model_type != "video":
        raise ConflictError("提示词预览只适用于视频模型")
    input_mode = payload.input_contract.get("input_mode")
    profile = resolve_model_prompt_profile(provider, model, input_mode)
    compiled = compile_model_prompt(
        payload.structured_script,
        profile=profile,
        input_contract=payload.input_contract,
        project_style=payload.project_style,
        voice_guidance=payload.voice_guidance,
        require_submission=True,
    )
    if (profile["recipe"].startswith("h3_") and payload.input_contract.get("ready") is True
            and not payload.input_contract.get("blockers")
            and not payload.input_contract.get("required_confirmations")
            and len(payload.structured_script.get("camera") or []) == 1):
        compiled["authoring_contract"] = build_h3_authoring_contract(
            payload.structured_script, profile=profile, input_contract=payload.input_contract,
            project_style=payload.project_style, voice_guidance=payload.voice_guidance,
        )
    if payload.authored_prompt is not None:
        authoring = compiled.get("authoring_contract")
        if authoring is None or not payload.authoring_source_fingerprint:
            raise ConflictError("仅就绪的单镜头 H3 改写可校验英文提示词, 且必须提交来源指纹")
        accepted = accept_h3_authored_result(
            payload.authored_prompt,
            contract={**authoring, "source_fingerprint": payload.authoring_source_fingerprint},
            script=payload.structured_script,
            profile=profile,
            input_contract=payload.input_contract,
            project_style=payload.project_style,
            voice_guidance=payload.voice_guidance,
        )
        compiled["authored_prompt_validation"] = accepted["validation"]
        compiled["authoring_result"] = accepted
    return compiled


@router.delete(
    "/{provider_id}/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_provider_model(
    provider_id: int, model_id: int, session: SessionDep, _admin: AdminUser
) -> None:
    await provider_service.get_provider(session, provider_id)
    model = await provider_service.get_model(session, provider_id, model_id)
    await provider_service.delete_model(session, model)
    await session.commit()
