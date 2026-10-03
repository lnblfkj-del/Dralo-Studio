"""Application protocol contracts, not a promise about a supplier's model catalogue."""
# ruff: noqa: RUF001
from urllib.parse import urlsplit, urlunsplit

from app.core.errors import ConflictError
from app.providers.toapis import is_toapis
from app.providers.video_contracts import VIDEO_CONTRACTS

PROTOCOL_CATALOG = [
    {"id": "minimax_video_v2", "name": "MiniMax H3 原生视频 V2", "model_types": ["video"], "default_path": "",
     "note": "官方 /v2/video_generation 协议；当前仅开放只读连通检查和模型登记，付费生成需完成素材公网地址、价格和提示词验收。"},
    {"id": "jimeng_video_t2v", "name": "即梦原生 3.0 文生（720p）", "model_types": ["video"], "default_path": "",
     "note": 'Visual API / AK-SK JSON 双密钥；仅 jimeng_t2v_v30，5/10 秒，固定 720p，不接收图片。'},
    {"id": "jimeng_video_pro", "name": "即梦原生 3.0 Pro（1080p）", "model_types": ["video"], "default_path": "",
     "note": 'Visual API / AK-SK JSON 双密钥；仅 jimeng_ti2v_v30_pro，文本或单首图，5/10 秒，固定 1080p；图生按上游支持画幅居中裁剪。'},
    {"id": "ark_video_images", "name": "方舟原生图生／首尾帧／多参考图", "model_types": ["video"], "default_path": "/api/v3",
     "note": "原生 content 图片角色及 Base64；首帧／首尾帧与普通参考图模式互斥，按模型能力启用。本地开放 PNG/JPEG、最多 9 图、编码合计 32 MB；参考视频／音频未开放。"},
    {"id": "kling_video_t2v", "name": "可灵原生文生视频", "model_types": ["video"], "default_path": "/v1",
     "note": "text2video；普通 API Key 或 AK/SK JSON，后者自动生成短期 JWT；模型参数范围需按官方能力配置。不是 Omni 视频编辑协议。"},
    {"id": "kling_video_i2v", "name": "可灵原生图生／首尾帧视频", "model_types": ["video"], "default_path": "/v1",
     "note": "image2video；首图／尾图 PNG/JPEG Base64，按模型能力启用；图生画幅跟随图片，不单独发送比例。多图请选独立协议。"},
    {"id": "kling_video_multi_image", "name": "可灵原生多参考图视频", "model_types": ["video"], "default_path": "/v1",
     "note": "multi-image2video / kling-v1-6；1–4 张 PNG/JPEG，5/10 秒。不是 Omni 的图像／视频／主体混合输入。"},
    {"id": "jimeng_video_first_last", "name": "即梦原生 3.0 首尾帧（720p）", "model_types": ["video"], "default_path": "",
     "note": 'Visual API / AK-SK 签名；密钥栏填写 {"access_key":"AK","secret_key":"SK"}，整体加密保存。仅 jimeng_i2v_first_tail_v30，5/10 秒，首尾帧 PNG/JPEG 同比例。不是方舟或即梦全版本。'},
    {"id": "ark_video_t2v", "name": "方舟原生文生视频", "model_types": ["video"], "default_path": "/api/v3",
     "note": "火山方舟 content-generation 异步任务；仅文生视频，不是即梦 OpenAPI。手动填写完整模型或接入点 ID；带图请选择方舟图生协议，参考音视频尚未开放。"},
    {"id": "dashscope_video_t2v", "name": "百炼原生文生视频", "model_types": ["video"], "default_path": "/api/v1",
     "note": "DashScope video-synthesis 文生格式，input.prompt + parameters.size；异步任务，不接受图像输入。地域域名和密钥必须匹配。"},
    {"id": "dashscope_video_i2v", "name": "百炼原生首图生视频", "model_types": ["video"], "default_path": "/api/v1",
     "note": "DashScope video-synthesis 首图格式，input.img_url + parameters.resolution；只接收一张无透明首图，当前本地上限 10 MB；不等于新版多模态协议。"},
    {"id": "sora_compatible", "name": "Sora 兼容视频（/videos）", "model_types": ["video"],
     "default_path": "/v1", "note": "multipart 提交 /videos；/videos/{id} 查询；/videos/{id}/content 下载。支持文本／单图，时长和尺寸需按实际模型配置；不同厂商扩展不自动推断。"},
    {"id": "newapi", "name": "New API 网关", "model_types": ["text", "image", "tts", "video"],
     "default_path": "/v1", "note": "Chat Completions、Images、Speech；视频为 /video/generations 通用协议，支持文本或单图；不是 /videos 的 Sora 格式。需按客户网关版本验证，不自动回退路径。"},
    {"id": "openai_compatible", "name": "OpenAI 兼容", "model_types": ["text", "image", "video", "tts"],
     "default_path": "/v1", "note": "文本 /chat/completions；图片 /images/generations、/images/edits；配音 /audio/speech。视频仅保留旧 /videos/generations 及 ToAPIs 专用适配，不代表通用视频协议。"},
    {"id": "anthropic_messages", "name": "Anthropic Messages", "model_types": ["text"],
     "default_path": "/v1", "note": "原生 /messages，x-api-key 鉴权；仅文本，不是 Claude Code 客户端协议。"},
    {"id": "google_gemini", "name": "Google Gemini 原生", "model_types": ["text"],
     "default_path": "/v1beta", "note": "原生 generateContent，x-goog-api-key 鉴权；当前仅文本。"},
]


def effective_protocol(provider, model=None):
    return getattr(model, "api_protocol", None) or provider.protocol


def effective_base_url(provider, model=None):
    raw = str(getattr(model, "api_base_url", None) or provider.base_url).rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConflictError("Base URL 只允许 HTTP(S) 服务地址，不得包含凭证、查询参数或片段")
    if effective_protocol(provider, model) == "minimax_video_v2":
        if parsed.scheme != "https" or parsed.hostname not in {"api.minimax.cn", "api.minimax.io"} or parsed.path or parsed.port not in (None, 443):
            raise ConflictError("MiniMax H3 原生协议请使用 https://api.minimax.cn 根地址，不带 /v1、/v2 或其他路径")
        return raw
    if effective_protocol(provider, model).startswith("jimeng_video_"):
        if parsed.scheme != "https" or parsed.path or parsed.port not in (None, 443):
            raise ConflictError("即梦原生入口使用 HTTPS 根域名，不带 /v1、路径或自定义端口，例如 https://visual.volcengineapi.com")
        return raw
    if not parsed.path:
        path = next((item["default_path"] for item in PROTOCOL_CATALOG if item["id"] == effective_protocol(provider, model)), "/v1")
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if effective_protocol(provider, model) in {"dashscope_video_t2v", "dashscope_video_i2v"} and not parsed.path.endswith("/api/v1"):
        raise ConflictError("百炼原生视频 Base URL 请填写地域服务域名或以 /api/v1 结尾的原生入口，不能使用 compatible-mode/v1；可只覆盖此视频模型的地址")
    if effective_protocol(provider, model) in {"ark_video_t2v", "ark_video_images"} and not parsed.path.endswith("/api/v3"):
        raise ConflictError("方舟原生视频 Base URL 请填写地域域名或以 /api/v3 结尾的入口；可只覆盖此视频模型的地址")
    return raw


def is_toapis_model(provider, model=None):
    return is_toapis(effective_base_url(provider, model), effective_protocol(provider, model))


def validate_protocol_type(protocol, model_type):
    supported = next((p["model_types"] for p in PROTOCOL_CATALOG if p["id"] == protocol), [])
    if protocol == "fake_video":
        supported = ["video"]
    if model_type not in supported:
        raise ConflictError("所选接口协议尚未实现该模型类型；请更换协议，未接入能力不能用于生成")


def validate_model_protocol(provider, model, *, configuration=False):
    if effective_protocol(provider, model) == "minimax_video_v2" and model.model_id not in {"MiniMax-H3", "MiniMax-H3-Max"}:
        raise ConflictError("MiniMax V2 视频协议只适配官方 MiniMax-H3 或 MiniMax-H3-Max 模型 ID")
    if effective_protocol(provider, model) == "kling_video_multi_image" and model.model_id != "kling-v1-6":
        raise ConflictError("当前可灵多参考图协议按官方文档仅开放 kling-v1-6；其他模型请使用对应原生协议")
    if effective_protocol(provider, model).startswith("jimeng_video_"):
        from app.providers.jimeng_video import SPECS
        required = SPECS[effective_protocol(provider, model)][0]
        if model.model_id != required:
            raise ConflictError(f"当前即梦原生协议仅适配 {required}")
    if configuration and model.model_type in {"audio", "embedding"} and not model.api_protocol:
        # Preserve legacy inventory/routing configuration, not executable capability.
        effective_base_url(provider, model)
        return
    validate_protocol_type(effective_protocol(provider, model), model.model_type)
    effective_base_url(provider, model)


def execution_contract(provider, model):
    """No credentials; freeze routing so recovery never silently changes protocol/model."""
    contract = {"provider_id": provider.id, "model_id": model.model_id,
                "protocol": effective_protocol(provider, model), "base_url": effective_base_url(provider, model)}
    if model.model_type == "video" and contract["protocol"] in VIDEO_CONTRACTS:
        contract["video_protocol_version"] = VIDEO_CONTRACTS[contract["protocol"]]
    return contract


async def model_confirmation_token(session, model):
    from app.models import Provider
    from app.services.canvas_generation_service import fingerprint
    provider = await session.get(Provider, model.provider_id)
    return fingerprint([model.model_id, model.default_params, model.capabilities, model.pricing,
                        execution_contract(provider, model), provider.enabled, model.enabled])
