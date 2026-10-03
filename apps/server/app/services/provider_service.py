"""模型渠道与模型定义业务逻辑。"""

import asyncio
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.core.outbound_http import outbound_client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.provider_crypto import api_key_hint, decrypt_api_key, encrypt_api_key, masked_api_key
from app.models import (
    MODEL_TYPE_AUDIO,
    MODEL_TYPE_IMAGE,
    MODEL_TYPE_TEXT,
    MODEL_TYPE_TTS,
    MODEL_TYPE_VIDEO,
    AgentSkill,
    AISettings,
    Provider,
    ProviderModel,
)
from app.providers.factory import create_provider_adapter
from app.providers.protocols import effective_base_url

provider_logger = get_logger("app.provider")

from app.services.provider_catalog_service import (  # noqa: F401
    PROVIDER_PRESETS,
    clear_default_text_model,
    create_model,
    create_provider,
    delete_provider,
    get_model,
    get_provider,
    is_builtin_provider,
    list_providers,
    update_model,
    update_provider,
)

async def get_default_text_model(session: AsyncSession) -> ProviderModel:
    model, _source = await resolve_default_model(session, MODEL_TYPE_TEXT)
    return model


async def get_ai_settings(session: AsyncSession) -> AISettings:
    from app.core.workspace_context import isolation_enabled
    settings = (await session.scalar(select(AISettings)) if isolation_enabled()
                else await session.get(AISettings, 1))
    if settings is None:
        settings = AISettings(
            **({} if isolation_enabled() else {"id": 1}),
            agent_skill_bindings={
                "outline": [],
                "script": [],
                "canvas": [],
                "market": [],
            },
        )
        session.add(settings)
        await session.flush()
    return settings


async def get_enabled_routed_model(
    session: AsyncSession, model_id: int | None, expected_type: str
) -> ProviderModel | None:
    if model_id is None:
        return None
    return await session.scalar(
        select(ProviderModel)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .where(
            ProviderModel.id == model_id,
            ProviderModel.model_type == expected_type,
            ProviderModel.enabled.is_(True),
            Provider.enabled.is_(True),
        )
    )


DEFAULT_MODEL_FIELDS = {
    MODEL_TYPE_TEXT: "default_text_model_id",
    MODEL_TYPE_IMAGE: "default_image_model_id",
    MODEL_TYPE_VIDEO: "default_video_model_id",
}

AGENT_MODEL_ROUTES = {
    ("outline", MODEL_TYPE_TEXT): ("outline_agent_text_model_id", "outline_agent_model_id"),
    ("script", MODEL_TYPE_TEXT): ("script_agent_text_model_id",),
    ("canvas", MODEL_TYPE_TEXT): ("canvas_agent_text_model_id", "canvas_agent_model_id"),
    ("canvas", MODEL_TYPE_IMAGE): ("canvas_agent_image_model_id",),
    ("canvas", MODEL_TYPE_VIDEO): ("canvas_agent_video_model_id",),
    ("canvas", MODEL_TYPE_AUDIO): ("canvas_agent_audio_model_id",),
    ("canvas", MODEL_TYPE_TTS): ("canvas_agent_tts_model_id",),
    ("market", MODEL_TYPE_TEXT): ("market_research_model_id",),
}

AGENT_ENABLED_FIELDS = {
    "outline": "outline_agent_enabled",
    "script": "script_agent_enabled",
    "canvas": "canvas_agent_enabled",
    "market": "market_research_enabled",
}

AGENT_LABELS = {
    "outline": "大纲 Agent",
    "script": "剧本 Agent",
    "canvas": "画布 Agent",
    "market": "市场探查 Agent",
}


async def _require_routed_model(
    session: AsyncSession,
    model_id: int,
    expected_type: str,
    *,
    route_label: str,
) -> ProviderModel:
    """Resolve an explicitly stored route without silently replacing it.

    A configured route is an operator decision. If it later becomes invalid or is
    disabled, falling through to another model hides a production configuration
    error and can also change cost or provider semantics.
    """
    pair = (
        await session.execute(
            select(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(ProviderModel.id == model_id)
        )
    ).one_or_none()
    if pair is None:
        raise ConflictError(f"{route_label}配置的模型不存在，请重新选择")
    model, provider = pair
    if model.model_type != expected_type:
        raise ConflictError(f"{route_label}配置的模型类型不匹配，请重新选择")
    if not provider.enabled:
        raise ConflictError(f"{route_label}配置的模型渠道已停用，请重新选择或启用渠道")
    if not model.enabled:
        raise ConflictError(f"{route_label}配置的模型已停用，请重新选择或启用模型")
    return model


async def resolve_default_model(
    session: AsyncSession, expected_type: str
) -> tuple[ProviderModel, str]:
    settings = await get_ai_settings(session)
    field = DEFAULT_MODEL_FIELDS.get(expected_type)
    configured_id = getattr(settings, field) if field else None
    label = {MODEL_TYPE_TEXT: "默认文本模型", MODEL_TYPE_IMAGE: "默认图片模型", MODEL_TYPE_VIDEO: "默认视频模型"}.get(
        expected_type, f"默认 {expected_type} 模型"
    )
    if configured_id is not None:
        return await _require_routed_model(
            session, configured_id, expected_type, route_label=label
        ), "global_default"
    # Text had an is_default flag before AISettings existed. Keep it only as a
    # compatibility fallback when no explicit global route has been configured.
    if expected_type == MODEL_TYPE_TEXT:
        model = await session.scalar(
            select(ProviderModel)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(
                ProviderModel.model_type == MODEL_TYPE_TEXT,
                ProviderModel.is_default.is_(True),
                ProviderModel.enabled.is_(True),
                Provider.enabled.is_(True),
            )
        )
        if model is not None:
            return model, "legacy_default"
    raise ConflictError(f"请先在 Ai 设置中配置{label}")


async def resolve_agent_model(
    session: AsyncSession, agent_key: str, model_type: str = MODEL_TYPE_TEXT
) -> tuple[ProviderModel, str]:
    settings = await get_ai_settings(session)
    enabled_field = AGENT_ENABLED_FIELDS.get(agent_key)
    if enabled_field is None or (agent_key, model_type) not in AGENT_MODEL_ROUTES:
        raise ConflictError("Agent 不支持所选模型类型")
    label = AGENT_LABELS[agent_key]
    if not getattr(settings, enabled_field):
        raise ConflictError(f"{label} 已由管理员停用")
    route_fields = AGENT_MODEL_ROUTES[(agent_key, model_type)]
    for index, field in enumerate(route_fields):
        model_id = getattr(settings, field)
        if model_id is not None:
            source = "agent" if index == 0 else "legacy_agent"
            return await _require_routed_model(
                session,
                model_id,
                model_type,
                route_label=f"{label} 的 {model_type} 路由",
            ), source
    if model_type in DEFAULT_MODEL_FIELDS:
        return await resolve_default_model(session, model_type)
    raise ConflictError(f"请先在系统设置为{label}配置 {model_type} 模型")


async def get_outline_agent_model(session: AsyncSession) -> ProviderModel:
    model, _source = await resolve_agent_model(session, "outline")
    return model


async def get_outline_agent_skill(session: AsyncSession) -> AgentSkill:
    return await get_agent_skill(session, "outline", output_modality=MODEL_TYPE_TEXT)


async def get_script_agent_model(session: AsyncSession) -> ProviderModel:
    model, _source = await resolve_agent_model(session, "script")
    return model


async def get_script_agent_skill(session: AsyncSession) -> AgentSkill:
    return await get_agent_skill(session, "script", output_modality=MODEL_TYPE_TEXT)


async def get_agent_skills(session: AsyncSession, agent_key: str) -> list[AgentSkill]:
    """Return enabled Skills in configured priority order, with legacy fallback."""
    if agent_key not in {"outline", "script", "canvas", "market"}:
        raise ConflictError("未知 Agent")
    settings = await get_ai_settings(session)
    binding_ids = list((settings.agent_skill_bindings or {}).get(agent_key, []))
    if not binding_ids:
        legacy_id = {
            "outline": settings.outline_agent_skill_id,
            "script": settings.script_agent_skill_id,
        }.get(agent_key)
        if legacy_id is not None:
            binding_ids = [legacy_id]
    if not binding_ids:
        return []
    rows = list((await session.execute(select(AgentSkill).where(AgentSkill.id.in_(binding_ids)))).scalars())
    by_id = {item.id: item for item in rows}
    return [by_id[item_id] for item_id in binding_ids if item_id in by_id and by_id[item_id].enabled and by_id[item_id].mode == agent_key]


async def get_agent_skill(
    session: AsyncSession,
    agent_key: str,
    *,
    required_tool: str | None = None,
    output_modality: str | None = None,
) -> AgentSkill:
    skills = await get_agent_skills(session, agent_key)
    for skill in skills:
        if output_modality is not None and skill.output_modality != output_modality:
            continue
        if required_tool is not None and required_tool not in skill.allowed_tools:
            continue
        return skill
    detail = f"且允许工具 {required_tool}" if required_tool else ""
    raise ConflictError(f"请先在 AI 控制中心为{AGENT_LABELS[agent_key]}绑定可用 Skill{detail}")


async def get_canvas_agent_model(session: AsyncSession) -> ProviderModel:
    model, _source = await resolve_agent_model(session, "canvas")
    return model


async def get_canvas_agent_audio_model(
    session: AsyncSession, *, text_to_speech: bool = False
) -> ProviderModel:
    expected_type = MODEL_TYPE_TTS if text_to_speech else MODEL_TYPE_AUDIO
    model, _source = await resolve_agent_model(session, "canvas", expected_type)
    return model


async def list_model_options(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .order_by(Provider.name, ProviderModel.name)
        )
    ).all()
    return [
        {
            "id": model.id,
            "provider_id": provider.id,
            "provider_name": provider.name,
            "model_id": model.model_id,
            "name": model.name,
            "model_type": model.model_type,
            "capabilities": model.capabilities,
            "default_params": model.default_params or {},
            "enabled": model.enabled and provider.enabled,
        }
        for model, provider in rows
    ]


async def describe_agent_routes(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Return a safe, non-secret view of every effective Agent route."""
    descriptions: dict[str, dict[str, Any]] = {}
    for agent_key, model_type in AGENT_MODEL_ROUTES:
        route_key = f"{agent_key}.{model_type}"
        try:
            model, source = await resolve_agent_model(session, agent_key, model_type)
            provider = await session.get(Provider, model.provider_id)
            descriptions[route_key] = {
                "ready": True,
                "source": source,
                "model_type": model_type,
                "effective_model_id": model.id,
                "provider_name": provider.name if provider is not None else None,
                "model_name": model.name,
                "model_id": model.model_id,
                "message": None,
            }
        except ConflictError as exc:
            descriptions[route_key] = {
                "ready": False,
                "source": "invalid",
                "model_type": model_type,
                "effective_model_id": None,
                "provider_name": None,
                "model_name": None,
                "model_id": None,
                "message": str(exc),
            }
    return descriptions


async def update_ai_settings(session: AsyncSession, data: dict[str, Any]) -> AISettings:
    settings = await get_ai_settings(session)
    routes = {
        "default_text_model_id": MODEL_TYPE_TEXT,
        "default_image_model_id": MODEL_TYPE_IMAGE,
        "default_video_model_id": MODEL_TYPE_VIDEO,
        "outline_agent_model_id": MODEL_TYPE_TEXT,
        "canvas_agent_model_id": MODEL_TYPE_TEXT,
        "outline_agent_text_model_id": MODEL_TYPE_TEXT,
        "script_agent_text_model_id": MODEL_TYPE_TEXT,
        "canvas_agent_text_model_id": MODEL_TYPE_TEXT,
        "canvas_agent_image_model_id": MODEL_TYPE_IMAGE,
        "canvas_agent_video_model_id": MODEL_TYPE_VIDEO,
        "canvas_agent_audio_model_id": MODEL_TYPE_AUDIO,
        "canvas_agent_tts_model_id": MODEL_TYPE_TTS,
        "market_research_model_id": MODEL_TYPE_TEXT,
    }
    for field, expected_type in routes.items():
        if field not in data:
            continue
        model_id = data[field]
        if model_id is not None:
            pair = (
                await session.execute(
                    select(ProviderModel, Provider)
                    .join(Provider, Provider.id == ProviderModel.provider_id)
                    .where(ProviderModel.id == model_id)
                )
            ).one_or_none()
            if pair is None or pair[0].model_type != expected_type:
                raise ConflictError(f"{field} 选择了错误类型的模型")
            model, provider = pair
            if not model.enabled or not provider.enabled:
                raise ConflictError(f"{field} 不能选择已停用的模型或渠道")
        setattr(settings, field, model_id)
    if "agent_skill_bindings" in data and data["agent_skill_bindings"] is not None:
        incoming = data["agent_skill_bindings"]
        allowed_agents = {"outline", "script", "canvas", "market"}
        if any(key not in allowed_agents for key in incoming):
            raise ConflictError("agent_skill_bindings 包含未知 Agent")
        normalized: dict[str, list[int]] = {}
        for agent_key in ("outline", "script", "canvas", "market"):
            ids = list(dict.fromkeys(incoming.get(agent_key, [])))
            if any(not isinstance(skill_id, int) or skill_id < 1 for skill_id in ids):
                raise ConflictError("Skill 绑定编号无效")
            if ids:
                skills = list((await session.execute(select(AgentSkill).where(AgentSkill.id.in_(ids)))).scalars())
                found = {skill.id: skill for skill in skills}
                if len(found) != len(ids) or any(not found[item].enabled or found[item].mode != agent_key for item in ids if item in found):
                    raise ConflictError(f"{AGENT_LABELS[agent_key]}只能绑定同类型且已启用的 Skill")
            normalized[agent_key] = ids
        settings.agent_skill_bindings = normalized
        # Keep legacy readers operational while the application migrates to multi-Skill binding.
        settings.outline_agent_skill_id = next(iter(normalized["outline"]), None)
        settings.script_agent_skill_id = next(iter(normalized["script"]), None)
    if "outline_agent_skill_id" in data:
        skill_id = data["outline_agent_skill_id"]
        if skill_id is not None:
            skill = await session.get(AgentSkill, skill_id)
            if (
                skill is None
                or not skill.enabled
                or skill.mode != "outline"
                or skill.output_modality != MODEL_TYPE_TEXT
                or set(skill.input_modalities) != {MODEL_TYPE_TEXT}
            ):
                raise ConflictError("outline_agent_skill_id 必须选择已启用的 outline/text Skill")
        settings.outline_agent_skill_id = skill_id
        bindings = dict(settings.agent_skill_bindings or {})
        current = [item for item in bindings.get("outline", []) if item != skill_id]
        bindings["outline"] = ([skill_id] if skill_id is not None else []) + current
        settings.agent_skill_bindings = bindings
    if "script_agent_skill_id" in data:
        skill_id = data["script_agent_skill_id"]
        if skill_id is not None:
            skill = await session.get(AgentSkill, skill_id)
            if (
                skill is None
                or not skill.enabled
                or skill.mode != "script"
                or skill.output_modality != MODEL_TYPE_TEXT
                or set(skill.input_modalities) != {MODEL_TYPE_TEXT}
            ):
                raise ConflictError("script_agent_skill_id 必须选择已启用的 script/text Skill")
        settings.script_agent_skill_id = skill_id
        bindings = dict(settings.agent_skill_bindings or {})
        current = [item for item in bindings.get("script", []) if item != skill_id]
        bindings["script"] = ([skill_id] if skill_id is not None else []) + current
        settings.agent_skill_bindings = bindings
    for field in (
        "outline_agent_enabled",
        "outline_agent_max_chunks",
        "outline_agent_instruction",
        "script_agent_enabled",
        "script_agent_instruction",
        "canvas_agent_enabled",
        "canvas_agent_instruction",
        "market_research_enabled",
        "market_research_instruction",
        "market_search_provider",
        "market_search_max_results",
        "market_search_timeout_seconds",
    ):
        if field in data:
            value = data[field]
            if isinstance(value, str):
                value = value.strip()
            setattr(settings, field, value)
    market_search_api_key = data.get("market_search_api_key")
    if market_search_api_key is not None:
        market_search_api_key = market_search_api_key.strip()
        settings.market_search_api_key_ciphertext = encrypt_api_key(market_search_api_key)
        settings.market_search_api_key_hint = api_key_hint(market_search_api_key)
    if data.get("clear_market_search_api_key"):
        settings.market_search_api_key_ciphertext = None
        settings.market_search_api_key_hint = None
    if "default_text_model_id" in data and data["default_text_model_id"] is not None:
        await clear_default_text_model(session, except_model_id=data["default_text_model_id"])
        model = await session.get(ProviderModel, data["default_text_model_id"])
        if model is not None:
            model.is_default = True
    await session.flush()
    return settings


async def to_ai_settings_out(session: AsyncSession) -> dict[str, Any]:
    settings = await get_ai_settings(session)
    stored_bindings = settings.agent_skill_bindings or {}
    bindings = {
        key: list(stored_bindings.get(key) or [])
        for key in ("outline", "script", "canvas", "market")
    }
    if not bindings.get("outline") and settings.outline_agent_skill_id:
        bindings["outline"] = [settings.outline_agent_skill_id]
    if not bindings.get("script") and settings.script_agent_skill_id:
        bindings["script"] = [settings.script_agent_skill_id]
    return {
        "default_text_model_id": settings.default_text_model_id,
        "default_image_model_id": settings.default_image_model_id,
        "default_video_model_id": settings.default_video_model_id,
        "agent_skill_bindings": bindings,
        "outline_agent_model_id": settings.outline_agent_model_id,
        "outline_agent_enabled": settings.outline_agent_enabled,
        "outline_agent_max_chunks": settings.outline_agent_max_chunks,
        "outline_agent_instruction": settings.outline_agent_instruction,
        "outline_agent_skill_id": settings.outline_agent_skill_id,
        "outline_agent_text_model_id": settings.outline_agent_text_model_id
        or settings.outline_agent_model_id,
        "script_agent_text_model_id": settings.script_agent_text_model_id,
        "script_agent_skill_id": settings.script_agent_skill_id,
        "script_agent_enabled": settings.script_agent_enabled,
        "script_agent_instruction": settings.script_agent_instruction,
        "canvas_agent_model_id": settings.canvas_agent_model_id,
        "canvas_agent_enabled": settings.canvas_agent_enabled,
        "canvas_agent_instruction": settings.canvas_agent_instruction,
        "canvas_agent_text_model_id": settings.canvas_agent_text_model_id
        or settings.canvas_agent_model_id,
        "canvas_agent_image_model_id": settings.canvas_agent_image_model_id,
        "canvas_agent_video_model_id": settings.canvas_agent_video_model_id,
        "canvas_agent_audio_model_id": settings.canvas_agent_audio_model_id,
        "canvas_agent_tts_model_id": settings.canvas_agent_tts_model_id,
        "market_research_model_id": settings.market_research_model_id,
        "market_research_enabled": settings.market_research_enabled,
        "market_research_instruction": settings.market_research_instruction,
        "market_search_provider": settings.market_search_provider,
        "market_search_api_key_hint": settings.market_search_api_key_hint,
        "market_search_max_results": settings.market_search_max_results,
        "market_search_timeout_seconds": settings.market_search_timeout_seconds,
        "models": await list_model_options(session),
        "resolved_routes": await describe_agent_routes(session),
    }


async def delete_model(session: AsyncSession, model: ProviderModel) -> None:
    await assert_no_active_jobs(session, model.provider_id, model.id)
    await session.delete(model)
    await session.flush()


async def assert_no_active_jobs(session, provider_id, model_id=None):
    from app.models import Job
    from app.services.job_service import TERMINAL_STATUSES
    query = select(Job.id).where(Job.provider_id == provider_id, Job.status.not_in(TERMINAL_STATUSES))
    if model_id is not None:
        query = query.where(Job.payload["provider_model_id"].as_integer() == model_id)
    if await session.scalar(query.limit(1)):
        raise ConflictError("该渠道或模型仍有排队/执行中的任务，请完成或取消后再修改路由配置或删除")


async def discover_models(provider: Provider) -> tuple[list[str], int]:
    adapter = create_provider_adapter(provider, decrypt_api_key(provider.api_key_ciphertext))
    return await adapter.discover_models()


async def check_connection(provider: Provider) -> dict:
    """A read-only endpoint probe. HTTP reachability is not model acceptance."""
    from app.core.errors import ProviderError, TimeoutError_
    from app.providers.protocols import effective_protocol
    key = decrypt_api_key(provider.api_key_ciphertext)
    protocol = effective_protocol(provider)
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if protocol == "anthropic_messages"
               else {"x-goog-api-key": key} if protocol == "google_gemini"
               else {"Authorization": f"Bearer {key}"})
    started = perf_counter()
    base_url = effective_base_url(provider)
    host = urlsplit(base_url).hostname or "未知主机"
    last_error: httpx.HTTPError | None = None
    probe_path = (
        "/v2/query/video_generation?page_num=1&page_size=1"
        if protocol == "minimax_video_v2" else "/models"
    )
    for attempt in range(2):
        try:
            async with outbound_client(timeout=provider.timeout_seconds, proxy=provider.proxy_url, follow_redirects=False) as client:
                async with client.stream("GET", base_url + probe_path, headers=headers) as response:
                    status = response.status_code
            break
        except httpx.HTTPError as exc:
            last_error = exc
        if attempt == 0:
            await asyncio.sleep(0.25)
    else:
        assert last_error is not None
        error_text = " ".join(str(item) for item in (last_error, last_error.__cause__) if item).lower()
        provider_logger.warning(
            "渠道连通性检查失败 provider_id=%s host=%s proxy=%s error=%s",
            provider.id, host, bool(provider.proxy_url), type(last_error).__name__,
        )
        details = {"host": host, "reason": "connection", "retry_count": 1}
        if isinstance(last_error, httpx.TimeoutException):
            details["reason"] = "timeout"
            raise TimeoutError_(f"连接 {host} 超时；请检查网络、渠道代理或延长超时时间", details=details) from last_error
        if "certificate" in error_text or "ssl" in error_text or "tls" in error_text:
            details["reason"] = "tls"
            message = f"{host} 的 TLS 证书校验失败；请检查系统时间、证书链或 HTTPS 代理"
        elif "name resolution" in error_text or "getaddrinfo" in error_text or "nodename" in error_text:
            details["reason"] = "dns"
            message = f"无法解析 {host}；请检查 DNS 或网络连接"
        elif "proxy" in error_text:
            details["reason"] = "proxy"
            message = f"无法通过渠道代理连接 {host}；请检查代理地址与端口"
        elif "reset" in error_text or "disconnect" in error_text:
            details["reason"] = "reset"
            message = f"{host} 重置了连接；已自动重试，请稍后再试或检查网络线路"
        else:
            message = f"无法连接 {host}；请检查网络、渠道代理及 Base URL"
            from app.core.runtime_environment import windows_token_restricted
            if windows_token_restricted():
                details["reason"] = "runtime_restricted"
                message = "后端运行在受限进程中，渠道连接失败；请从正常 Windows 终端启动服务，或为启动操作批准联网权限"
        raise ProviderError(message, details=details) from last_error
    message = "端点已响应；这不代表模型发现或生成能力已验证"
    if protocol == "minimax_video_v2" and status == 200:
        message = "MiniMax H3 V2 只读查询已通过；不代表生成权限、价格或素材输入已验收"
    if status in {401, 403}:
        message = "端点已响应，但凭证或访问权限被拒绝"
    elif status == 404:
        message = "网络可达，但模型列表路径不存在；请核对协议及 Base URL"
    elif 300 <= status < 400:
        message = "端点返回重定向；为保护密钥未跟随，请填写最终 API 地址"
    elif status >= 400:
        message = "端点已响应，但渠道返回错误；不代表可生成"
    return {"http_status": status, "latency_ms": round((perf_counter() - started)*1000), "message": message}


async def test_provider_model(
    provider: Provider, model: ProviderModel, data: dict[str, Any], owner_id: int | None = None
) -> dict[str, Any]:
    """Explicitly confirmed synchronous text probe. Media uses durable canvas jobs."""
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != MODEL_TYPE_TEXT:
        raise ConflictError("媒体真实测试请前往画布创建任务，以保留任务编号、确认费用和恢复查询；本接口不再直接提交媒体生成")
    if not data.get("confirmed"):
        raise ConflictError("真实模型测试可能产生费用，请确认后提交")
    adapter = create_provider_adapter(provider, decrypt_api_key(provider.api_key_ciphertext), model)
    prompt = data["prompt"]
    references = data.get("reference_images", [])
    if any(not item.startswith("data:image/") for item in references):
        raise ConflictError("测试参考图格式无效")
    if sum(len(item) for item in references) > 28 * 1024 * 1024:
        raise ConflictError("测试参考图总大小不能超过 20 MB")
    from app.services import execution_policy_service, text_model_policy_service

    parameters, text_policy = text_model_policy_service.resolve(
        model,
        provider,
        data.get("parameters", {}),
        execution_policy_service.current_snapshot(),
    )
    started = perf_counter()

    if model.model_type == MODEL_TYPE_TEXT:
        from app.services import billing_service
        call_id = await billing_service.begin_test(owner_id, provider, model, prompt, parameters) if owner_id is not None else None
        try:
            result = await text_model_policy_service.execute(
                adapter,
                model=model.model_id,
                prompt=prompt,
                parameters=parameters,
                policy=text_policy,
            )
            await billing_service.observe(call_id, billing_service.usage_meter(result))
            text_model_policy_service.ensure_complete_result(result, policy=text_policy)
        finally:
            await billing_service.observe(call_id)
        return {
            "model_type": model.model_type,
            "text": result.get("text"),
            "latency_ms": round((perf_counter() - started) * 1000),
            "usage": result.get("usage", {}),
        }



def to_provider_out(provider: Provider) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "protocol": provider.protocol,
        "base_url": provider.base_url,
        "api_key_masked": masked_api_key(provider.api_key_hint),
        "timeout_seconds": provider.timeout_seconds,
        "proxy_url": provider.proxy_url,
        "max_concurrency": provider.max_concurrency,
        "enabled": provider.enabled,
        "is_builtin": is_builtin_provider(provider),
        "created_by": provider.created_by,
        "models": provider.models,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }
