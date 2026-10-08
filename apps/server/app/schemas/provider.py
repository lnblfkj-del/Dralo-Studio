"""模型渠道与模型定义请求响应。"""

from datetime import datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.models.provider import MODEL_TYPES, PROTOCOL_OPENAI_COMPATIBLE, PROVIDER_PROTOCOLS
from app.schemas.common import ORMModel


def validate_model_values(value, *, pricing=False):
    if value is None:
        raise ValueError("配置必须是 JSON 对象")
    if pricing:
        from app.services.pricing_service import validate_pricing
        validate_pricing(value)
        for key, amount in value.items():
            if key.endswith("_cents") and (type(amount) not in (int, float) or not isfinite(amount) or amount < 0):
                raise ValueError("价格必须是非负有限数值，单位为分；未知价格请移除字段")
    else:
        if {"api_key", "authorization", "headers", "base_url", "model", "messages", "stream"}.intersection(value):
            raise ValueError("默认参数不能覆盖密钥、请求地址、模型、消息或流式协议")
        if "video_prompt_certifications" in value:
            raise ValueError("视频提示词认证须单独配置，不能放入会发送到渠道的默认参数")
        for key in ("durations", "aspect_ratios", "resolutions"):
            if key in value and not isinstance(value[key], list):
                raise ValueError(f"{key} 必须是数组")
        if "max_reference_images" in value and (type(value["max_reference_images"]) is not int or not 0 <= value["max_reference_images"] <= 32):
            raise ValueError("参考图数量必须为 0 到 32 的整数")
        if "supports_negative_prompt" in value and type(value["supports_negative_prompt"]) is not bool:
            raise ValueError("负向提示词参数支持状态必须是布尔值")
        if any(type(item) not in (int, float) or not isfinite(item) or item <= 0 for item in value.get("durations", [])):
            raise ValueError("支持时长必须是正数数组，单位秒")
    return value


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = PROTOCOL_OPENAI_COMPATIBLE
    base_url: HttpUrl
    api_key: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(default=300, ge=5, le=600)
    proxy_url: HttpUrl | None = None
    max_concurrency: int = Field(default=8, ge=1, le=100)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("protocol")
    @classmethod
    def supported_protocol(cls, value: str) -> str:
        if value not in PROVIDER_PROTOCOLS:
            raise ValueError("不支持的模型协议")
        return value


class ProviderUpdate(BaseModel):
    protocol: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    proxy_url: HttpUrl | None = None
    clear_proxy: bool = False
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None

    @field_validator("protocol")
    @classmethod
    def supported_protocol(cls, value):
        if value is None or value not in PROVIDER_PROTOCOLS:
            raise ValueError("不支持的模型协议")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProviderModelCreate(BaseModel):
    api_protocol: str | None = None
    api_base_url: HttpUrl | None = None
    model_id: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    model_type: str
    capabilities: list[str] = Field(default_factory=list)
    default_params: dict[str, Any] = Field(default_factory=dict)
    video_prompt_certifications: dict[str, Any] = Field(default_factory=dict)
    pricing: dict[str, Any] = Field(default_factory=dict)
    max_concurrency: int = Field(default=8, ge=1, le=100)
    enabled: bool = True
    is_default: bool = False

    @field_validator("default_params")
    @classmethod
    def valid_params(cls, value):
        return validate_model_values(value)

    @field_validator("video_prompt_certifications")
    @classmethod
    def valid_video_prompt_certifications(cls, value):
        from app.services.video_prompt_compiler import validate_video_prompt_certifications

        return validate_video_prompt_certifications(value)

    @field_validator("pricing")
    @classmethod
    def valid_pricing(cls, value):
        return validate_model_values(value, pricing=True)

    @field_validator("api_protocol")
    @classmethod
    def supported_protocol(cls, value):
        if value is not None and value not in PROVIDER_PROTOCOLS:
            raise ValueError("不支持的模型协议")
        return value

    @field_validator("model_id")
    @classmethod
    def clean_model_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("model_type")
    @classmethod
    def supported_model_type(cls, value: str) -> str:
        if value not in MODEL_TYPES:
            raise ValueError("不支持的模型类型")
        return value


class ProviderModelUpdate(BaseModel):
    api_protocol: str | None = None
    api_base_url: HttpUrl | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    model_type: str | None = None
    capabilities: list[str] | None = None
    default_params: dict[str, Any] | None = None
    video_prompt_certifications: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None
    is_default: bool | None = None

    @field_validator("default_params")
    @classmethod
    def valid_params(cls, value):
        return validate_model_values(value)

    @field_validator("video_prompt_certifications")
    @classmethod
    def valid_video_prompt_certifications(cls, value):
        from app.services.video_prompt_compiler import validate_video_prompt_certifications

        return validate_video_prompt_certifications(value)

    @field_validator("pricing")
    @classmethod
    def valid_pricing(cls, value):
        return validate_model_values(value, pricing=True)

    @field_validator("api_protocol")
    @classmethod
    def supported_protocol(cls, value):
        return ProviderModelCreate.supported_protocol(value)

    @field_validator("model_id")
    @classmethod
    def clean_optional_model_id(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("model_type")
    @classmethod
    def supported_model_type(cls, value: str | None) -> str | None:
        if value is not None and value not in MODEL_TYPES:
            raise ValueError("不支持的模型类型")
        return value


class ProviderModelTestRequest(BaseModel):
    confirmed: bool = False
    prompt: str = Field(min_length=1, max_length=20000)
    mode: str = Field(default="text", max_length=64)
    reference_images: list[str] = Field(default_factory=list, max_length=4)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderModelTestOut(BaseModel):
    model_type: str
    text: str | None = None
    media_data_url: str | None = None
    mime_type: str | None = None
    latency_ms: int
    usage: dict[str, Any] = Field(default_factory=dict)


class ProviderModelOut(ORMModel):
    api_protocol: str | None = None
    api_base_url: str | None = None
    id: int
    provider_id: int
    model_id: str
    name: str
    model_type: str
    capabilities: list[str]
    default_params: dict[str, Any]
    video_prompt_certifications: dict[str, Any]
    pricing: dict[str, Any]
    max_concurrency: int
    effective_concurrency: int
    rate_limit_hits: int
    success_streak: int
    rate_limit_until: datetime | None
    last_rate_limited_at: datetime | None
    enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ProviderOut(ORMModel):
    id: int
    name: str
    protocol: str
    base_url: str
    api_key_masked: str
    timeout_seconds: int
    proxy_url: str | None
    max_concurrency: int
    enabled: bool
    is_builtin: bool
    created_by: int
    models: list[ProviderModelOut]
    created_at: datetime
    updated_at: datetime


class ProviderDiscoveryOut(BaseModel):
    models: list[str]
    latency_ms: int


class ProviderVideoContractOut(BaseModel):
    id: str
    name: str
    protocol: str
    input_modes: list[str]
    capabilities: list[str]
    default_params: dict[str, Any]
    api_base_url: str | None = None
    model_id_hint: str | None = None
    model_id_locked: bool = False
    note: str


class ProviderPresetOut(BaseModel):
    id: str
    name: str
    description: str
    base_url: str
    protocol: str = PROTOCOL_OPENAI_COMPATIBLE
    capabilities: list[str]
    configurable: bool = True
    status_note: str | None = None
    video_contracts: list[ProviderVideoContractOut] = Field(default_factory=list)


class ModelOptionOut(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    model_id: str
    name: str
    model_type: str
    capabilities: list[str]
    default_params: dict[str, Any]
    enabled: bool


class AgentRouteResolutionOut(BaseModel):
    ready: bool
    source: str
    model_type: str
    effective_model_id: int | None
    provider_name: str | None
    model_name: str | None
    model_id: str | None
    message: str | None


class AISettingsUpdate(BaseModel):
    default_text_model_id: int | None = Field(default=None, ge=1)
    default_image_model_id: int | None = Field(default=None, ge=1)
    default_video_model_id: int | None = Field(default=None, ge=1)
    agent_skill_bindings: dict[str, list[int]] | None = None
    outline_agent_model_id: int | None = Field(default=None, ge=1)
    outline_agent_enabled: bool | None = None
    outline_agent_max_chunks: int | None = Field(default=None, ge=1, le=8)
    outline_agent_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    outline_agent_skill_id: int | None = Field(default=None, ge=1)
    script_agent_text_model_id: int | None = Field(default=None, ge=1)
    script_agent_skill_id: int | None = Field(default=None, ge=1)
    script_agent_enabled: bool | None = None
    script_agent_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    canvas_agent_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_enabled: bool | None = None
    canvas_agent_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    outline_agent_text_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_text_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_image_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_video_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_audio_model_id: int | None = Field(default=None, ge=1)
    canvas_agent_tts_model_id: int | None = Field(default=None, ge=1)
    market_research_model_id: int | None = Field(default=None, ge=1)
    market_research_enabled: bool | None = None
    market_research_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    market_search_provider: str | None = Field(default=None, pattern="^(auto|native|tavily)$")
    market_search_api_key: str | None = Field(default=None, min_length=8, max_length=512)
    clear_market_search_api_key: bool = False
    market_search_max_results: int | None = Field(default=None, ge=3, le=20)
    market_search_timeout_seconds: int | None = Field(default=None, ge=5, le=60)


class AISettingsOut(BaseModel):
    default_text_model_id: int | None
    default_image_model_id: int | None
    default_video_model_id: int | None
    agent_skill_bindings: dict[str, list[int]]
    outline_agent_model_id: int | None
    outline_agent_enabled: bool
    outline_agent_max_chunks: int
    outline_agent_instruction: str
    outline_agent_skill_id: int | None
    script_agent_text_model_id: int | None
    script_agent_skill_id: int | None
    script_agent_enabled: bool
    script_agent_instruction: str
    canvas_agent_model_id: int | None
    canvas_agent_enabled: bool
    canvas_agent_instruction: str
    outline_agent_text_model_id: int | None
    canvas_agent_text_model_id: int | None
    canvas_agent_image_model_id: int | None
    canvas_agent_video_model_id: int | None
    canvas_agent_audio_model_id: int | None
    canvas_agent_tts_model_id: int | None
    market_research_model_id: int | None
    market_research_enabled: bool
    market_research_instruction: str
    market_search_provider: str
    market_search_api_key_hint: str | None
    market_search_max_results: int
    market_search_timeout_seconds: int
    models: list[ModelOptionOut]
    resolved_routes: dict[str, AgentRouteResolutionOut]
