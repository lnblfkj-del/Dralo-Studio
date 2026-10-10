"""根据持久化协议创建 Provider Adapter。"""

from app.core.errors import ProviderError
from app.models import (
    PROTOCOL_FAKE_VIDEO,
    PROTOCOL_GOOGLE_GEMINI,
    PROTOCOL_OPENAI_COMPATIBLE,
    Provider,
)
from app.providers.anthropic import AnthropicMessagesProvider
from app.providers.fake_video import FakeVideoProvider
from app.providers.google_gemini import GoogleGeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.protocols import effective_base_url, effective_protocol, validate_model_protocol
from app.providers.toapis import ToAPIsProvider, is_toapis


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
    if protocol in AUDIO_PROTOCOLS:
        from app.providers.audio_transport import NativeAudioProvider
        return NativeAudioProvider(protocol=protocol, **options)
    if protocol in {"meaicc_video", "meaicc_video_images"}:
        from app.providers.meaicc_video import MeaiccVideoProvider
        return MeaiccVideoProvider(image_mode=protocol == "meaicc_video_images", **options)
    if protocol == "newapi":
        from app.providers.newapi import NewAPIProvider
        return NewAPIProvider(**options)
    if protocol == "minimax_video_v2":
        from app.providers.minimax_video_v2 import MiniMaxVideoV2Transport
        return MiniMaxVideoV2Transport(**options)
    if protocol.startswith("kling_video_"):
        from app.providers.kling_video import KlingVideoProvider
        return KlingVideoProvider(protocol=protocol, **options)
    if protocol in {"ark_video_t2v", "ark_video_images"}:
        from app.providers.ark_video import ArkVideoProvider
        return ArkVideoProvider(image_mode=protocol == "ark_video_images", **options)
    if protocol.startswith("jimeng_video_"):
        from app.providers.jimeng_video import JimengVideoProvider
        return JimengVideoProvider(protocol=protocol, **options)
    if protocol in {"dashscope_video_t2v", "dashscope_video_i2v"}:
        from app.providers.dashscope_video import DashScopeVideoProvider
        return DashScopeVideoProvider(image_mode=protocol == "dashscope_video_i2v", **options)
    if protocol == "sora_compatible":
        from app.providers.newapi import SoraCompatibleProvider
        return SoraCompatibleProvider(**options)
    if protocol == PROTOCOL_OPENAI_COMPATIBLE:
        if is_toapis(options["base_url"], protocol):
            return ToAPIsProvider(**options)
        return OpenAICompatibleProvider(**options)
    if protocol == "anthropic_messages":
        return AnthropicMessagesProvider(**options)
    if protocol == PROTOCOL_GOOGLE_GEMINI:
        return GoogleGeminiProvider(**options)
    if protocol == PROTOCOL_FAKE_VIDEO:
        return FakeVideoProvider(**options)
    raise ProviderError("模型渠道协议不受支持")
