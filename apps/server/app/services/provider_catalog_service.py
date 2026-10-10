"""Provider presets plus provider/model catalog mutation operations."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.provider_crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.models import MODEL_TYPE_TEXT, MODEL_TYPE_VIDEO, Provider, ProviderModel
from app.providers.protocols import effective_base_url, is_toapis_model, validate_model_protocol
from app.providers.video_contracts import supplier_video_templates


async def _assert_no_active_jobs(
    session: AsyncSession, provider_id: int, model_id: int | None = None
) -> None:
    from app.services.provider_service import assert_no_active_jobs

    await assert_no_active_jobs(session, provider_id, model_id)


PROVIDER_PRESETS = [
    {"id":"jimeng", "name":"即梦 Visual API", "description":"即梦原生 AK/SK 签名，不是方舟入口。",
     "base_url":"https://visual.volcengineapi.com", "protocol":"jimeng_video_t2v", "capabilities":["video"],
     "status_note":"已接入文生 720p、首尾帧 720p、Pro 1080p；在模型上选择对应原生协议及固定 req_key"},
    {"id":"newapi", "name":"New API 自建网关", "description":"填写客户自己的网关地址，不预设第三方服务。",
     "base_url":"", "protocol":"newapi", "capabilities":["text","image","tts","video"], "configurable":True,
     "status_note":"基础兼容协议；视频需区分通用 video/generations 与 Sora /videos，具体网关版本需验收"},
    {
        "id": "anthropic", "name": "Anthropic", "description": "原生 Messages 文本接口；不是 Claude Code 客户端。",
        "base_url": "https://api.anthropic.com/v1", "protocol": "anthropic_messages", "capabilities": ["text"],
        "status_note": "适配器契约已接入，实际模型可用性需单独验证",
    },
    {
        "id": "toapis",
        "name": "ToAPIs",
        "description": "OpenAI-compatible 聚合渠道；可通过 Responses API 实测原生 Web Search。",
        "base_url": "https://toapis.com/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text", "responses", "web_search"],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "OpenAI 官方 API，支持文本与图片模型。",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text", "image"],
    },
    {
        "id": "google_ai_studio",
        "name": "Google AI Studio",
        "description": "Google Gemini 原生 API，支持模型发现与文本生成。",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "protocol": "google_gemini",
        "capabilities": ["text"],
    },
    {
        "id": "xiaomi_mimo",
        "name": "小米 MiMo",
        "description": "小米 MiMo 官方 API，兼容 OpenAI Chat Completions，并提供全模态理解与语音模型。",
        "base_url": "https://api.xiaomimimo.com/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text", "image", "audio", "video", "tts"],
    },
    {
        "id": "vertex_ai",
        "name": "Vertex AI",
        "description": "Google Cloud Vertex AI，需 OAuth 与项目区域配置。",
        "base_url": "https://aiplatform.googleapis.com",
        "protocol": "openai_compatible",
        "capabilities": ["text", "image", "video"],
        "configurable": True,
        "status_note": "需接入 Google Cloud OAuth",
    },
    {
        "id": "volcengine",
        "name": "火山方舟",
        "description": "火山引擎方舟模型服务。",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "protocol": "openai_compatible",
        "capabilities": ["text"],
        "status_note": "默认兼容文本；视频模型可覆盖为方舟原生文生或图生协议；图片原生扩展需单独验收",
    },
    {
        "id": "volcengine_agent",
        "name": "火山方舟 Agent Plan",
        "description": "方舟智能体专用协议，与当前大纲 Agent 架构不同。",
        "base_url": "https://ark.cn-beijing.volces.com",
        "protocol": "openai_compatible",
        "capabilities": ["agent"],
        "configurable": True,
        "status_note": "专用 Agent 协议待接入",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "description": "DeepSeek 官方 OpenAI-compatible API。",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text"],
    },
    {
        "id": "grok",
        "name": "Grok / xAI",
        "description": "xAI 官方 API，使用 OpenAI-compatible 请求格式。",
        "base_url": "https://api.x.ai/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text", "image"],
    },
    {
        "id": "vidu",
        "name": "Vidu",
        "description": "Vidu 视频生成官方接口。",
        "base_url": "https://api.vidu.cn",
        "protocol": "openai_compatible",
        "capabilities": ["video"],
        "configurable": True,
        "status_note": "视频专用协议待 M6E 接入",
    },
    {
        "id": "dashscope",
        "name": "阿里百炼",
        "description": "阿里云百炼模型服务，支持文本、图片与视频模型；多媒体生成使用百炼专用异步端点。",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text"],
        "status_note": "默认兼容文本；视频模型可覆盖为百炼文生或首图协议及 /api/v1 地址；图片原生扩展待接入",
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "description": "MiniMax 文本、语音和视频服务。",
        "base_url": "https://api.minimax.cn",
        "protocol": "minimax_video_v2",
        "capabilities": ["video"],
        "configurable": True,
        "status_note": "H3 V2 只读连通和模型登记已接入；生产视频需完成价格、素材和提示词验收",
    },
    {
        "id": "kling",
        "name": "可灵 Kling",
        "description": "可灵图片与视频生成服务。",
        "base_url": "https://api-beijing.klingai.com",
        "protocol": "kling_video_t2v",
        "capabilities": ["video"],
        "configurable": True,
        "status_note": "视频原生 API Key/JWT 已接入；图生首尾帧、多参考图请选择对应模型协议；Omni 和图片生成待适配",
    },
    {
        "id": "agnes",
        "name": "Agnes",
        "description": "Agnes AI 官方模型服务；文本使用 OpenAI 兼容接口。",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text"],
        "configurable": True,
        "status_note": "文本兼容接口已确认；图片扩展参数及视频 video_id 查询仍待专用适配，暂不声明支持",
    },
    {
        "id": "moonshot",
        "name": "Moonshot AI",
        "description": "Moonshot AI 官方 OpenAI-compatible API。",
        "base_url": "https://api.moonshot.cn/v1",
        "protocol": "openai_compatible",
        "capabilities": ["text"],
    },
]

for _preset in PROVIDER_PRESETS:
    _preset["video_contracts"] = supplier_video_templates(_preset["id"])


def is_builtin_provider(provider: Provider) -> bool:
    """固定供应商目录中的渠道不可删除；自定义渠道保持可管理。"""
    provider_url = provider.base_url.rstrip("/")
    return any(
        provider.name == preset["name"]
        or (preset.get("base_url") and provider_url == str(preset["base_url"]).rstrip("/"))
        for preset in PROVIDER_PRESETS
        if preset["id"] != "toapis"
    )


async def list_providers(session: AsyncSession) -> list[Provider]:
    stmt = select(Provider).options(selectinload(Provider.models)).order_by(Provider.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_provider(session: AsyncSession, provider_id: int) -> Provider:
    provider = await session.scalar(
        select(Provider).where(Provider.id == provider_id).options(selectinload(Provider.models))
    )
    if provider is None:
        raise NotFoundError("模型渠道不存在")
    return provider


async def create_provider(session: AsyncSession, created_by: int, data: dict[str, Any]) -> Provider:
    if await session.scalar(select(Provider.id).where(Provider.name == data["name"])):
        raise ConflictError("模型渠道名称已存在")
    api_key = data.pop("api_key")
    if str(data.get("protocol", "")).startswith("jimeng_video_"):
        from app.providers.jimeng_video import credentials
        credentials(api_key)
    provider = Provider(
        created_by=created_by,
        api_key_ciphertext=encrypt_api_key(api_key),
        api_key_hint=api_key_hint(api_key),
        **data,
    )
    effective_base_url(provider)
    session.add(provider)
    await session.flush()
    return await get_provider(session, provider.id)


async def update_provider(
    session: AsyncSession, provider: Provider, data: dict[str, Any]
) -> Provider:
    if any(key in data and data[key] != getattr(provider, key) for key in ("base_url", "protocol", "proxy_url")) or data.get("clear_proxy") or data.get("api_key"):
        await _assert_no_active_jobs(session, provider.id)
    api_key = data.pop("api_key", None)
    if data.get("protocol", provider.protocol).startswith("jimeng_video_"):
        from app.providers.jimeng_video import credentials
        credentials(api_key if api_key is not None else decrypt_api_key(provider.api_key_ciphertext))
    clear_proxy = data.pop("clear_proxy", False)
    if "name" in data and data["name"] != provider.name:
        if await session.scalar(select(Provider.id).where(Provider.name == data["name"])):
            raise ConflictError("模型渠道名称已存在")
    for key, value in data.items():
        setattr(provider, key, value)
    if clear_proxy:
        provider.proxy_url = None
    if api_key is not None:
        provider.api_key_ciphertext = encrypt_api_key(api_key)
        provider.api_key_hint = api_key_hint(api_key)
    effective_base_url(provider)
    if "protocol" in data:
        for model in provider.models:
            validate_model_protocol(provider, model, configuration=True)
    await session.flush()
    return await get_provider(session, provider.id)


async def delete_provider(session: AsyncSession, provider: Provider) -> None:
    await _assert_no_active_jobs(session, provider.id)
    if is_builtin_provider(provider):
        raise ConflictError("系统固定供应商不可删除，仅自定义渠道支持删除")
    await session.delete(provider)
    await session.flush()


async def create_model(
    session: AsyncSession, provider: Provider, data: dict[str, Any]
) -> ProviderModel:
    if "_audio_verification" in data.get("default_params", {}):
        raise ConflictError("音频账户核验记录只能由服务端生成")
    if "episode_planning_capability" in data.get("default_params", {}):
        raise ConflictError("请先创建视频模型，再通过规划规格入口登记能力和证据")
    if data.get("video_prompt_certifications"):
        raise ConflictError("请先创建视频模型，再通过提示词认证入口登记验收证据")
    if not data.get("model_type"):
        raise ConflictError("请选择模型类型，不能默认归为文本")
    if data["model_type"] != MODEL_TYPE_TEXT and data.get("is_default"):
        raise ConflictError("只有文本模型可以设为默认创作模型")
    if data.get("video_prompt_certifications") and data["model_type"] != MODEL_TYPE_VIDEO:
        raise ConflictError("视频提示词认证只能配置在视频模型上")
    if data.get("api_base_url") is not None:
        data["api_base_url"] = str(data["api_base_url"])
    from app.services.text_model_policy_service import validate_defaults

    data["default_params"] = validate_defaults(
        data["model_type"], data.get("default_params", {})
    )
    if data["model_type"] == MODEL_TYPE_TEXT:
        defaults = data["default_params"]
        policy = {"request_timeout_seconds": 300, **defaults.get("_text_execution", {})}
        data["default_params"] = validate_defaults(
            data["model_type"], {**defaults, "_text_execution": policy}
        )
    candidate = ProviderModel(provider_id=provider.id, **data)
    validate_model_protocol(provider, candidate, configuration=True)
    exists = await session.scalar(
        select(ProviderModel.id).where(
            ProviderModel.provider_id == provider.id,
            ProviderModel.model_id == data["model_id"],
        )
    )
    if exists:
        raise ConflictError("该模型已添加")
    if not data.get("name"):
        data["name"] = data["model_id"]
    data["effective_concurrency"] = data.get("max_concurrency", 8)
    from app.providers.toapis import VIDEO_MODELS, model_defaults
    if data["model_type"] == MODEL_TYPE_VIDEO and is_toapis_model(provider, candidate) and data["model_id"] in VIDEO_MODELS:
        data["default_params"] = {**model_defaults(data["model_id"]), **data.get("default_params", {})}
    if data["model_type"] == MODEL_TYPE_TEXT:
        has_default = await session.scalar(
            select(ProviderModel.id).where(
                ProviderModel.model_type == MODEL_TYPE_TEXT,
                ProviderModel.is_default.is_(True),
            )
        )
        data["is_default"] = data.get("is_default", False) or has_default is None
        if data["is_default"]:
            await clear_default_text_model(session)
    model = ProviderModel(provider_id=provider.id, **data)
    session.add(model)
    await session.flush()
    return model


async def get_model(session: AsyncSession, provider_id: int, model_id: int) -> ProviderModel:
    model = await session.get(ProviderModel, model_id)
    if model is None or model.provider_id != provider_id:
        raise NotFoundError("模型不存在")
    return model


async def update_model(
    session: AsyncSession, model: ProviderModel, data: dict[str, Any]
) -> ProviderModel:
    if "video_prompt_certifications" in data:
        if data["video_prompt_certifications"] != (model.video_prompt_certifications or {}):
            raise ConflictError("提示词认证须通过独立入口维护，普通模型编辑不能覆盖验收记录")
        data.pop("video_prompt_certifications")
    if "default_params" in data:
        from app.services.audio_verification_service import KEY
        saved_audio = (model.default_params or {}).get(KEY)
        if KEY in data["default_params"] and data["default_params"][KEY] != saved_audio:
            raise ConflictError("普通编辑不能覆盖音频核验记录")
        if saved_audio is not None:
            data["default_params"] = {**data["default_params"], KEY: saved_audio}
        from app.services.episode_planning_capability import CONFIG_KEY

        stored = (model.default_params or {}).get(CONFIG_KEY)
        supplied = data["default_params"].get(CONFIG_KEY)
        if CONFIG_KEY in data["default_params"] and supplied != stored:
            raise ConflictError("规划规格须通过独立入口维护，普通默认参数不能覆盖验证记录")
        if stored is not None:
            data["default_params"] = {**data["default_params"], CONFIG_KEY: stored}
    if data.get("api_base_url") is not None:
        data["api_base_url"] = str(data["api_base_url"])
    next_type = data.get("model_type", model.model_type)
    if (data.get("video_prompt_certifications", model.video_prompt_certifications)
            and next_type != MODEL_TYPE_VIDEO):
        raise ConflictError("视频提示词认证只能配置在视频模型上")
    if "default_params" in data or "model_type" in data:
        from app.services.text_model_policy_service import validate_defaults

        data["default_params"] = validate_defaults(
            next_type, data.get("default_params", model.default_params)
        )
    routing = {"api_protocol", "api_base_url", "model_id", "model_type", "default_params", "capabilities"}
    if any(key in data and data[key] != getattr(model, key) for key in routing):
        await _assert_no_active_jobs(session, model.provider_id, model.id)
    next_model_id = data.get("model_id")
    if next_model_id and next_model_id != model.model_id:
        exists = await session.scalar(
            select(ProviderModel.id).where(
                ProviderModel.provider_id == model.provider_id,
                ProviderModel.model_id == next_model_id,
                ProviderModel.id != model.id,
            )
        )
        if exists:
            raise ConflictError("该模型标识已存在")
    if data.get("is_default"):
        if data.get("model_type", model.model_type) != MODEL_TYPE_TEXT:
            raise ConflictError("只有文本模型可以设为默认创作模型")
        await clear_default_text_model(session, except_model_id=model.id)
    if data.get("model_type", model.model_type) != MODEL_TYPE_TEXT:
        data["is_default"] = False
    if "max_concurrency" in data:
        # An administrator changing the ceiling starts a fresh safe window. This
        # is explicit and avoids leaving an old adaptive value above the ceiling.
        data["effective_concurrency"] = int(data["max_concurrency"])
        data["rate_limit_hits"] = 0
        data["success_streak"] = 0
        data["rate_limit_until"] = None
    for key, value in data.items():
        setattr(model, key, value)
    if routing.intersection(data) or data.get("enabled") is True:
        provider = await session.get(Provider, model.provider_id)
        validate_model_protocol(provider, model, configuration=True)
    await session.flush()
    return model


async def clear_default_text_model(
    session: AsyncSession, except_model_id: int | None = None
) -> None:
    models = list(
        (
            await session.execute(
                select(ProviderModel).where(
                    ProviderModel.model_type == MODEL_TYPE_TEXT,
                    ProviderModel.is_default.is_(True),
                )
            )
        ).scalars()
    )
    for item in models:
        if item.id != except_model_id:
            item.is_default = False


