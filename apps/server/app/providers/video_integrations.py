"""Registered video transports and their channel rules, independent of creative workflows."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType

from app.core.errors import ConflictError


@dataclass(frozen=True)
class VideoIntegration:
    key: str
    version: str
    module: str
    adapter_class: str
    validator: str
    image_mode: bool | None = None
    protocol_argument: bool = False
    audio_parameter: str | None = None
    client_business_id: bool = False
    minimum_poll_seconds: int = 0
    staged_request: bool = False
    persistent: bool = True

    def create_adapter(self, **options):
        if self.image_mode is not None:
            options["image_mode"] = self.image_mode
        if self.protocol_argument:
            options["protocol"] = self.key
        cls = getattr(import_module(f"app.providers.{self.module}"), self.adapter_class)
        return cls(**options)

    def validate_parameters(self, model_id, parameters, *, first=False, last=False, references=0,
                            negative_prompt=None, preview_only=False, h3_authorized=False):
        flags = {"first": first, "last": last, "references": references}
        if self.staged_request:
            if not preview_only and not h3_authorized:
                raise ConflictError("MiniMax H3 生产提交尚未开放；请先完成片段英文提示词审核")
            from app.providers.minimax_video_parameters import validate_h3_video_parameters

            validate_h3_video_parameters(model_id, parameters, negative_prompt=negative_prompt, **flags)
            return parameters
        if self.validator == "fake":
            return parameters
        if self.validator == "legacy":
            if first or last or references:
                raise ConflictError("当前视频适配器没有已注册的图像输入协议")
            return parameters
        if self.validator == "toapis":
            if negative_prompt and model_id not in {"wan2.6", "kling-v2-6"}:
                raise ConflictError("此 ToAPIs 视频协议未声明负面提示词，请合并到生成要求中")
            from app.providers.toapis import video_parameters

            return video_parameters(model_id, parameters, **flags)
        validator = getattr(import_module(f"app.providers.{self.module}"), "video_parameters")
        if self.validator in {"meaicc", "ark"}:
            return validator(parameters, negative_prompt=negative_prompt, image_mode=self.image_mode, **flags)
        if self.validator == "kling":
            return validator(self.key, parameters, negative_prompt=negative_prompt, **flags)
        if self.validator == "jimeng":
            return validator(parameters, protocol=self.key, negative_prompt=negative_prompt, **flags)
        if self.validator == "dashscope":
            return validator(parameters, image_mode=self.image_mode, **flags)
        if self.validator == "sora" and negative_prompt:
            raise ConflictError("Sora 兼容视频请将负面提示词合并到生成要求中")
        if self.validator not in {"newapi", "sora"}:
            raise ConflictError("视频接入的参数校验器未注册，已停止提交")
        return validator(parameters, sora=self.validator == "sora", **flags)

    def prepare_prompt(self, model_id, prompt, negative_prompt):
        negative = (negative_prompt or "").strip()
        if self.validator == "toapis" and negative and model_id not in {"wan2.6", "kling-v2-6"}:
            return f"{prompt.rstrip()}\n\n生成限制（必须遵守）：{negative}", None
        return prompt, negative_prompt

    def submission_mode(self, model_id, input_mode):
        if self.validator == "toapis" and model_id == "grok-video-1.5":
            return {"text": "text_to_video", "first_frame": "first_frame_image_to_video",
                    "single_image": "reference_images_to_video", "multi_reference": "reference_images_to_video"}[input_mode]
        return input_mode

    def audio_field(self, model_id):
        if self.validator == "toapis":
            return ("generate_audio" if model_id == "seedance-2-5" else "audio"
                    if model_id in {"kling-v3-omni", "kling-v2-6", "wan2.6", "wan2.6-flash"} else None)
        return self.audio_parameter

    def optional_style_reference_limit(self, model_id):
        return 7 if self.validator == "toapis" and model_id == "kling-v3-omni" else None

    def normalize_rejected_submission(self, payload):
        """Only proven rejection may clear submit intent; an unknown response never can."""
        if not self.client_business_id:
            return payload
        submission = dict(payload.get("video_submission") or {})
        if not submission.get("started") or submission.get("id"):
            return payload
        for attempt in reversed(payload.get("attempt_history") or []):
            diagnostic = attempt.get("provider_diagnostic") or {}
            path = diagnostic.get("endpoint_path", "")
            if diagnostic.get("endpoint_host") not in {"toapis.cn", "toapis.com"}:
                break
            if (path == "/v1/videos/generations" and diagnostic.get("http_status") in {400, 422}
                    and diagnostic.get("provider_error_code") == "invalid_request"):
                business_id = submission.pop("business_id", None)
                return {**payload, "video_submission": {
                    **submission, "started": False, "rejection": diagnostic,
                    **({"rejected_business_id": business_id} if business_id else {}),
                }}
            # Historical recovery may have queried an explicitly rejected business ID.
            if (diagnostic.get("http_status") == 404
                    and diagnostic.get("provider_error_code") == "task_not_found"
                    and submission.get("business_id")
                    and path == f"/v1/videos/generations/{submission['business_id']}"):
                continue
            break
        return payload

    def prompt_recipe_error(self, model_id, recipe):
        toapis = self.validator == "toapis"
        if recipe.startswith("h3_") and not self.staged_request:
            return "H3 模型与当前官方 V2 视频协议不匹配"
        if recipe.startswith("seedance_") and not (
            self.validator == "ark" or (toapis and model_id == "seedance-2-5")
        ):
            return "Seedance 模型与当前视频协议不匹配"
        if recipe == "kling" and not (
            self.validator == "kling" or (toapis and model_id in {"kling-v2-6", "kling-v3-omni"})
        ):
            return "可灵模型与当前视频协议不匹配"
        if self.validator == "legacy" or self.validator == "fake":
            return "当前视频协议没有已注册的模型适配器"
        if toapis:
            from app.providers.toapis import VIDEO_MODELS

            if model_id not in VIDEO_MODELS:
                return "当前视频协议没有已注册的模型适配器"
        return None


_REGISTERED = (
    VideoIntegration("meaicc_video_images", "meaicc.images.v1", "meaicc_video", "MeaiccVideoProvider", "meaicc", image_mode=True),
    VideoIntegration("meaicc_video", "meaicc.text.v1", "meaicc_video", "MeaiccVideoProvider", "meaicc", image_mode=False),
    VideoIntegration("minimax_video_v2", "minimax.h3.video.v2.registration-only", "minimax_video_v2", "MiniMaxVideoV2Transport", "minimax", staged_request=True),
    VideoIntegration("kling_video_t2v", "kling.text2video.v1", "kling_video", "KlingVideoProvider", "kling", protocol_argument=True, audio_parameter="generate_audio"),
    VideoIntegration("kling_video_i2v", "kling.image2video.v1", "kling_video", "KlingVideoProvider", "kling", protocol_argument=True, audio_parameter="generate_audio"),
    VideoIntegration("kling_video_multi_image", "kling.multi-image2video.v1", "kling_video", "KlingVideoProvider", "kling", protocol_argument=True),
    VideoIntegration("jimeng_video_first_last", "jimeng.first_last.v30.720p.v1", "jimeng_video", "JimengVideoProvider", "jimeng", protocol_argument=True),
    VideoIntegration("jimeng_video_t2v", "jimeng.t2v.v30.720p.v1", "jimeng_video", "JimengVideoProvider", "jimeng", protocol_argument=True),
    VideoIntegration("jimeng_video_pro", "jimeng.ti2v.v30.pro.1080p.v1", "jimeng_video", "JimengVideoProvider", "jimeng", protocol_argument=True),
    VideoIntegration("ark_video_t2v", "ark.t2v.v1", "ark_video", "ArkVideoProvider", "ark", image_mode=False, audio_parameter="generate_audio"),
    VideoIntegration("ark_video_images", "ark.images.v1", "ark_video", "ArkVideoProvider", "ark", image_mode=True, audio_parameter="generate_audio"),
    VideoIntegration("newapi", "generic.v1", "newapi", "NewAPIProvider", "newapi"),
    VideoIntegration("sora_compatible", "sora.multipart.v1", "newapi", "SoraCompatibleProvider", "sora"),
    VideoIntegration("dashscope_video_t2v", "dashscope.t2v.v1", "dashscope_video", "DashScopeVideoProvider", "dashscope", image_mode=False, audio_parameter="audio"),
    VideoIntegration("dashscope_video_i2v", "dashscope.i2v.v1", "dashscope_video", "DashScopeVideoProvider", "dashscope", image_mode=True, audio_parameter="audio"),
    VideoIntegration("openai_compatible", "openai_compatible", "openai_compatible", "OpenAICompatibleProvider", "legacy"),
    VideoIntegration("fake_video", "fake_video", "fake_video", "FakeVideoProvider", "fake", audio_parameter="generate_audio", persistent=False),
)
VIDEO_INTEGRATIONS = MappingProxyType({item.key: item for item in _REGISTERED})
TOAPIS_VIDEO = VideoIntegration("toapis", "toapis.video.v1", "toapis", "ToAPIsProvider", "toapis",
                               client_business_id=True, minimum_poll_seconds=5)


def resolve_video_integration(provider, model=None):
    from app.providers.protocols import effective_protocol, is_toapis_model

    protocol = effective_protocol(provider, model)
    if protocol == "custom_video":
        from app.providers.configured_video import connection_version
        from app.schemas.video_connection import VideoConnection

        raw = (getattr(model, "video_config", None) or {}).get("integration")
        if raw is None:
            raise ConflictError("自定义视频接法尚未填写，请导入或编辑接法配置")
        connection = VideoConnection.model_validate(raw).model_dump(mode="json")
        return ConfiguredVideoIntegration("custom_video", connection_version(connection), "configured_video",
                                          "ConfiguredVideoProvider", "configured", connection=connection)
    if protocol == "openai_compatible" and is_toapis_model(provider, model):
        return TOAPIS_VIDEO
    integration = VIDEO_INTEGRATIONS.get(protocol)
    if integration is None:
        raise ConflictError("视频协议没有注册接入实现；请先配置对应的视频接法")
    return integration


@dataclass(frozen=True)
class ConfiguredVideoIntegration(VideoIntegration):
    connection: dict | None = None

    def create_adapter(self, **options):
        from app.providers.configured_video import ConfiguredVideoProvider

        return ConfiguredVideoProvider(connection=self.connection, **options)

    def validate_parameters(self, model_id, parameters, *, first=False, last=False, references=0,
                            negative_prompt=None, **_options):
        from app.providers.configured_video import validate_mapping
        from app.schemas.video_connection import VideoConnection

        return validate_mapping(VideoConnection.model_validate(self.connection), parameters, first=first,
                                last=last, references=references, negative_prompt=negative_prompt)

    def audio_field(self, model_id):
        from app.providers.configured_video import sources

        mapped = sources(self.connection["request"])
        return next((field for field in ("generate_audio", "audio") if field in mapped or f"param.{field}" in mapped), None)

    def prompt_recipe_error(self, model_id, recipe):
        return None


def canonical_legacy_parameters(contract, parameters):
    """Read historical Omni resolution aliases without granting additional specifications."""
    result = dict(parameters)
    if (contract.get("model_id") == "kling-v3-omni"
            and isinstance(result.get("resolution"), str) and result["resolution"] in {"std", "pro"}):
        result["resolution"] = {"std": "720p", "pro": "1080p"}[result["resolution"]]
    return result
