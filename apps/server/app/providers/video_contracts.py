"""Shared video contract registry for provider templates and job creation."""
from app.core.errors import ConflictError
from app.providers.video_integrations import VIDEO_INTEGRATIONS

VIDEO_CONTRACTS = {key: item.version for key, item in VIDEO_INTEGRATIONS.items()
                   if item.validator not in {"legacy", "fake"}}

VIDEO_PROTOCOL_TEMPLATES = {
    "meaicc_video_images": {
        "name": "MEAICC 图生／参考图（待实测）",
        "input_modes": ["first_frame", "first_last_frame", "single_image", "multi_reference"],
        "capabilities": ["image_to_video", "first_last_frame", "multi_reference"],
        "default_params": {"durations": list(range(4, 16)), "resolutions": ["768p"],
                           "aspect_ratios": ["16:9", "9:16", "1:1"], "max_reference_images": 9,
                           "supports_first_frame": True, "supports_last_frame": True,
                           "supported_video_input_modes": ["first_frame", "first_last_frame", "single_image", "multi_reference"]},
        "api_base_url": "https://api.meaicc.com/v1", "model_id_hint": "mx-h3",
        "note": "首尾帧或普通参考图不可混用；上传到渠道 minioapi.meaicc.com，参考音视频未开放。",
    },
    "meaicc_video": {
        "name": "MEAICC 文生视频",
        "input_modes": ["text"], "capabilities": ["text_to_video"],
        "default_params": {"durations": list(range(4, 16)), "resolutions": ["768p"],
                           "aspect_ratios": ["16:9", "9:16", "1:1"], "max_reference_images": 0,
                           "supported_video_input_modes": ["text"]},
        "api_base_url": "https://api.meaicc.com/v1", "model_id_hint": "mx-h3",
        "note": "mx-h3 为 768p；sd-2-c4 需改为 720p。仅文生视频，素材上传尚未开放。",
    },
    "minimax_video_v2": {
        "name": "MiniMax H3 原生视频 V2（待生产验收）",
        "input_modes": ["text"],
        "capabilities": ["text_to_video"],
        "default_params": {"durations": list(range(4, 16)), "resolutions": ["768P", "2K"],
                           "aspect_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                           "max_reference_images": 0, "supported_video_input_modes": ["text"]},
        "api_base_url": "https://api.minimax.cn",
        "model_id_hint": "MiniMax-H3",
        "note": "已登记官方 V2 路由；模型需保持停用，待价格、提示词和素材链路验收后再开放生成。H3-Max 需另建模型并配置 5–15 秒、480P/768P。",
    },
    "jimeng_video_t2v": {
        "name": "即梦 3.0 文生 720p",
        "input_modes": ["text"],
        "capabilities": ["text_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p"], "aspect_ratios": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"], "max_reference_images": 0, "supported_video_input_modes": ["text"]},
        "model_id_hint": "jimeng_t2v_v30", "model_id_locked": True,
        "note": "固定 req_key；只接收文本。",
    },
    "jimeng_video_first_last": {
        "name": "即梦 3.0 首尾帧 720p",
        "input_modes": ["first_last_frame"],
        "capabilities": ["first_last_frame"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p"], "supports_first_frame": True, "supports_last_frame": True, "max_reference_images": 0, "supported_video_input_modes": ["first_last_frame"]},
        "model_id_hint": "jimeng_i2v_first_tail_v30", "model_id_locked": True,
        "note": "固定 req_key；必须同时提供同画幅首帧和尾帧。",
    },
    "jimeng_video_pro": {
        "name": "即梦 3.0 Pro 1080p",
        "input_modes": ["text", "first_frame", "single_image"],
        "capabilities": ["text_to_video", "image_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["1080p"], "aspect_ratios": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"], "supports_first_frame": True, "max_reference_images": 1, "supported_video_input_modes": ["text", "first_frame", "single_image"]},
        "model_id_hint": "jimeng_ti2v_v30_pro", "model_id_locked": True,
        "note": "固定 req_key；支持文本或一张首图，不支持尾帧和多图。",
    },
    "ark_video_t2v": {
        "name": "方舟原生文生视频",
        "input_modes": ["text"], "capabilities": ["text_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"], "max_reference_images": 0, "supported_video_input_modes": ["text"]},
        "note": "填写官方模型或推理接入点 ID；具体时长必须按模型收窄。",
    },
    "ark_video_images": {
        "name": "方舟图生／首尾帧／多参考图",
        "input_modes": ["first_frame", "single_image", "multi_reference", "first_last_frame"],
        "capabilities": ["image_to_video", "first_last_frame", "multi_reference"],
        "default_params": {"durations": [5, 10], "resolutions": ["480p", "720p", "1080p"], "aspect_ratios": ["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"], "supports_first_frame": True, "supports_last_frame": True, "max_reference_images": 9, "supported_video_input_modes": ["first_frame", "single_image", "multi_reference", "first_last_frame"]},
        "note": "首尾帧与普通参考图互斥；9 张是适配器上限，具体模型可更低。",
    },
    "dashscope_video_t2v": {
        "name": "百炼原生文生视频",
        "input_modes": ["text"], "capabilities": ["text_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1"], "max_reference_images": 0, "supported_video_input_modes": ["text"]},
        "api_base_url": "https://dashscope.aliyuncs.com/api/v1",
        "note": "当前适配 video-synthesis 文生协议；模型、地域地址和密钥必须一致。",
    },
    "dashscope_video_i2v": {
        "name": "百炼原生单首图视频",
        "input_modes": ["first_frame", "single_image"],
        "capabilities": ["image_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["480p", "720p", "1080p"], "supports_first_frame": True, "max_reference_images": 1, "supported_video_input_modes": ["first_frame", "single_image"]},
        "api_base_url": "https://dashscope.aliyuncs.com/api/v1",
        "note": "当前只接收一张无透明首图；万相 3.0 多模态 media 协议尚未接入。",
    },
    "kling_video_t2v": {
        "name": "可灵原生文生视频",
        "input_modes": ["text"], "capabilities": ["text_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1"], "max_reference_images": 0, "supported_video_input_modes": ["text"]},
        "note": "text2video；具体时长和清晰度按所填模型收窄。",
    },
    "kling_video_i2v": {
        "name": "可灵原生图生／首尾帧",
        "input_modes": ["first_frame", "single_image", "first_last_frame"],
        "capabilities": ["image_to_video", "first_last_frame"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p", "1080p"], "supports_first_frame": True, "supports_last_frame": True, "max_reference_images": 1, "supported_video_input_modes": ["first_frame", "single_image", "first_last_frame"]},
        "note": "image2video；图生画幅跟随首图，多图必须使用独立多图协议。",
    },
    "kling_video_multi_image": {
        "name": "可灵 1.6 多参考图",
        "input_modes": ["single_image", "multi_reference"],
        "capabilities": ["multi_reference"],
        "default_params": {"durations": [5, 10], "resolutions": ["720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1"], "max_reference_images": 4, "supported_video_input_modes": ["single_image", "multi_reference"]},
        "model_id_hint": "kling-v1-6", "model_id_locked": True,
        "note": "仅 kling-v1-6；需要 1–4 张普通参考图，不接收首尾帧。",
    },
    "sora_compatible": {
        "name": "Sora /videos 文生与单图参考",
        "input_modes": ["text", "first_frame", "single_image"],
        "capabilities": ["text_to_video", "image_to_video"],
        "default_params": {"durations": [4, 8, 12], "resolutions": ["720p", "1024p"], "aspect_ratios": ["16:9", "9:16"], "supports_first_frame": True, "max_reference_images": 1, "supported_video_input_modes": ["text", "first_frame", "single_image"]},
        "model_id_hint": "sora-2",
        "note": "multipart /videos；基础契约只发送文本或一张 input_reference。",
    },
    "newapi": {
        "name": "New API 通用视频",
        "input_modes": ["text", "first_frame", "single_image"],
        "capabilities": ["text_to_video", "image_to_video"],
        "default_params": {"durations": [5, 10], "resolutions": ["480p", "720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1"], "supports_first_frame": True, "max_reference_images": 1, "supported_video_input_modes": ["text", "first_frame", "single_image"]},
        "note": "通用 /video/generations；必须确认客户网关版本，不代表 Sora /videos。",
    },
}

SUPPLIER_VIDEO_PROTOCOLS = {
    "minimax": ["minimax_video_v2"],
    "jimeng": ["jimeng_video_t2v", "jimeng_video_first_last", "jimeng_video_pro"],
    "volcengine": ["ark_video_t2v", "ark_video_images"],
    "dashscope": ["dashscope_video_t2v", "dashscope_video_i2v"],
    "kling": ["kling_video_t2v", "kling_video_i2v", "kling_video_multi_image"],
    "openai": ["sora_compatible"],
    "newapi": ["newapi", "sora_compatible"],
}


def supplier_video_templates(supplier_id):
    """Return JSON-safe executable contracts, never inferred marketing capabilities."""
    return [
        {"id": protocol, "protocol": protocol, **VIDEO_PROTOCOL_TEMPLATES[protocol]}
        for protocol in SUPPLIER_VIDEO_PROTOCOLS.get(supplier_id, [])
    ]


def validate_video_parameters(protocol, parameters, *, first=False, last=False, references=0, negative_prompt=None):
    if protocol not in VIDEO_CONTRACTS:
        raise ConflictError("视频协议没有注册校验器")
    return VIDEO_INTEGRATIONS[protocol].validate_parameters(
        None, parameters, first=first, last=last, references=references, negative_prompt=negative_prompt,
    )
