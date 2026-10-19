"""根据持久化协议创建 Provider Adapter。"""

from app.core.errors import ProviderError
from app.models import (
    PROTOCOL_GOOGLE_GEMINI,
    PROTOCOL_OPENAI_COMPATIBLE,
    Provider,
)
from app.providers.anthropic import AnthropicMessagesProvider
from app.providers.google_gemini import GoogleGeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.protocols import effective_base_url, effective_protocol, validate_model_protocol
from app.providers.toapis import ToAPIsProvider, is_toapis
from app.providers.video_integrations import VIDEO_INTEGRATIONS, resolve_video_integration


def create_provider_adapter(provider: Provider, api_key: str, model=None):
    if model is not None:
        validate_model_protocol(provider, model)
    protocol = effective_protocol(provider, model)
    options = {
        "base_url": effective_base_url(provider, model),
        "api_key": api_key,
        "timeout_seconds": provider.timeout_seconds,
        "proxy_url": provider.proxy_url,
    }
    from app.providers.audio_contracts import AUDIO_PROTOCOLS
    if protocol in {"openai_responses", "ollama_chat"}:
        from app.providers.structured_text import StructuredTextProvider
        return StructuredTextProvider(protocol=protocol, **options)
    if protocol in AUDIO_PROTOCOLS:
        from app.providers.audio_transport import NativeAudioProvider
        return NativeAudioProvider(protocol=protocol, **options)
    if protocol == "custom_video" or (protocol in VIDEO_INTEGRATIONS and protocol != PROTOCOL_OPENAI_COMPATIBLE):
        return resolve_video_integration(provider, model).create_adapter(**options)
    if protocol == PROTOCOL_OPENAI_COMPATIBLE:
        if getattr(model, "model_type", None) == "video":
            return resolve_video_integration(provider, model).create_adapter(**options)
        if is_toapis(options["base_url"], protocol):
            return ToAPIsProvider(**options)
        return OpenAICompatibleProvider(**options)
    if protocol == "anthropic_messages":
        return AnthropicMessagesProvider(**options)
    if protocol == PROTOCOL_GOOGLE_GEMINI:
        return GoogleGeminiProvider(**options)
    raise ProviderError("模型渠道协议不受支持")
