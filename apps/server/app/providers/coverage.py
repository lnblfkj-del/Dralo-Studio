"""Read-only implementation coverage; discovery is never production certification."""
from app.core.errors import ConflictError
from app.providers.protocols import effective_protocol, is_toapis_model, validate_model_protocol
from app.providers.toapis import VIDEO_MODELS


def model_coverage(provider, model):
    status, scope = "protocol_supported", "基础协议已实现；高级功能按能力配置校验"
    try:
        validate_model_protocol(provider, model)
        if model.model_type == "video":
            if effective_protocol(provider, model) == "meaicc_video_images":
                status, scope = "partial", "MEAICC 图生／参考图：独立素材上传、任务查询下载已接入，待真实图生验收；参考音视频未开放"
            elif effective_protocol(provider, model) == "meaicc_video":
                scope = "MEAICC 独立 JSON /videos：仅文生视频、原任务查询和下载；参考图片／音视频尚未开放"
            elif effective_protocol(provider, model) == "minimax_video_v2":
                status, scope = "partial", "MiniMax H3 V2 已接入只读连通与任务通信层；视频提交、媒体地址、价格及正式提示词尚未开放"
            elif effective_protocol(provider, model).startswith("kling_video_"):
                scope = "可灵独立文生／图生／多图视频合同：API Key 或 JWT、任务查询下载；具体模式需按模型配置；Omni 参考视频与编辑未开放"
            elif effective_protocol(provider, model).startswith("jimeng_video_"):
                scope = "即梦独立 req_key 合同：文生 720p／首尾帧 720p／Pro 文生或单首图 1080p；AK/SK 签名、查询、下载，5/10 秒；不等于全部版本"
            elif effective_protocol(provider, model) == "ark_video_images":
                scope = "方舟原生首图／首尾帧／多参考图：本地 Base64 与角色显式传入；视频／音频与编辑协议尚未开放"
            elif effective_protocol(provider, model) == "ark_video_t2v":
                scope = "方舟原生文生视频：提交、查询、下载已接入；带图请选方舟图生协议，参考音视频未开放，不是即梦 OpenAPI"
            elif effective_protocol(provider, model) in {"dashscope_video_t2v", "dashscope_video_i2v"}:
                scope = "百炼原生 video-synthesis：明确选择文生或单首图模式；异步头、任务查询、下载已接入。新版多模态、参考音频／视频未适配"
            elif effective_protocol(provider, model) == "sora_compatible":
                scope = "Sora 兼容 multipart /videos：文本／单图；任务查询及认证内容下载；厂商扩展需独立验收"
            elif effective_protocol(provider, model) == "newapi":
                scope = "New API 通用视频 v1：文本／单图，提交、查询、下载；不是 Sora 格式，尾帧／多图未支持"
            elif is_toapis_model(provider, model) and model.model_id in VIDEO_MODELS:
                scope = "专用视频提交、查询、下载；图片输入受各模型规则限制"
                if model.model_id == "wan2.6":
                    scope = "文生视频／单图生视频；5/10/15 秒，720p/1080p；参考视频未接入"
                elif model.model_id == "wan2.6-flash":
                    scope = "单图生视频（必须有图）；5/10/15 秒，720p/1080p、audio/watermark；参考视频模式未接入"
                elif model.model_id == "seedance-2-5":
                    scope = "文生／单首图／普通参考图：显式 image_with_roles，4–30 秒；自动时长首尾帧、参考音视频、编辑延长尚未开放"
                elif model.model_id == "veo3.1-fast":
                    scope = "文生／首尾帧／最多 3 张普通参考图；固定 8 秒，metadata 分辨率及 generation_type；不是官方直连版"
            else:
                status, scope = "partial", "仅保留历史视频路径；未经专用协议验收，不支持图片输入"
        elif model.model_type == "text":
            scope = "文本请求协议已实现；工具调用、多模态和结构化输出需分别验收"
        elif model.model_type == "tts":
            scope = "语音合成接口；不等于音乐生成、语音识别或音色克隆已适配"
            if effective_protocol(provider, model) in {"stepfun_tts", "minimax_audio_subscription", "elevenlabs_tts"}:
                status, scope = "partial", "原生配音画布提交及持久结果保存恢复已接入，音色须核验；资产库入口、媒体探测及真实验收待完成"
        elif model.model_type == "audio" and effective_protocol(provider, model) in {"stepfun_music", "elevenlabs_music"}:
            status, scope = "partial", "纯器乐画布提交、持久查询和结果保存恢复已接入；资产库入口、媒体探测及真实验收待完成，不是 TTS 或环境音接口"
        elif model.model_type == "image":
            scope = "OpenAI 图片生成／编辑协议；不等于所有厂商异步图片协议已适配"
    except ConflictError as exc:
        status, scope = "unsupported", exc.message
    return {"provider_id": provider.id, "provider_name": provider.name,
            "model_id": model.id, "model_name": model.model_id, "model_type": model.model_type,
            "protocol": effective_protocol(provider, model), "status": status, "scope": scope,
            "verification": "not_certified", "verification_note": "未建立当前路由的完整真实验收记录；连接测试成功不等于商用认证"}
