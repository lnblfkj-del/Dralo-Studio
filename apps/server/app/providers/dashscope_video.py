"""DashScope video-synthesis native HTTP contracts (explicit text/image modes)."""
import base64
import binascii
import io
from urllib.parse import quote, urlsplit

import httpx
from app.core.outbound_http import outbound_client
from PIL import Image, UnidentifiedImageError

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus

PROTOCOLS = {"dashscope_video_t2v", "dashscope_video_i2v"}


def video_parameters(parameters, *, image_mode, first=False, last=False, references=0):
    count = int(first) + references
    if last or (image_mode and count != 1) or (not image_mode and count):
        raise ConflictError("百炼图生视频需且仅需一张首图；文生视频不能带图片，当前协议不接收尾帧")
    for key in ("input", "img_url", "image", "image_urls", "audio_url", "reference_urls", "metadata", "content"):
        if parameters.get(key):
            raise ConflictError("请使用受权限校验的节点素材引用，不允许扩展参数直接传入媒体")
    result = {}
    for key in ("duration", "seed"):
        value = parameters.get(key)
        if value is None:
            continue
        if type(value) is not int or not (1 <= value <= 30 if key == "duration" else 0 <= value <= 2147483647):
            raise ConflictError(f"百炼 {key} 超出基础协议范围；请按模型文档配置")
        result[key] = value
    for key in ("prompt_extend", "watermark", "audio"):
        if parameters.get(key) is not None:
            if type(parameters[key]) is not bool:
                raise ConflictError(f"{key} 必须为布尔值")
            result[key] = parameters[key]
    if parameters.get("shot_type") is not None:
        if parameters["shot_type"] not in ("single", "multi"):
            raise ConflictError("shot_type 必须是 single 或 multi")
        if parameters.get("prompt_extend") is False:
            raise ConflictError("百炼 shot_type 需要开启 prompt_extend，不能静默忽略")
        result["shot_type"] = parameters["shot_type"]
    if parameters.get("n", 1) != 1 or any(parameters.get(k) is not None for k in ("fps", "generate_audio", "width", "height", "size")):
        raise ConflictError("百炼视频请使用单任务、duration、比例和分辨率；fps/size 等额外映射尚未开放")
    resolution = parameters.get("resolution")
    ratio = parameters.get("aspect_ratio")
    default = (None, "", "default", "模型默认")
    if image_mode:
        if resolution not in default:
            if str(resolution).upper() not in {"480P", "720P", "1080P"}:
                raise ConflictError("百炼图生视频分辨率只开放 480P/720P/1080P；以模型能力为准")
            result["resolution"] = str(resolution).upper()
    elif ratio not in default or resolution not in default:
        sizes = {("16:9","720p"):"1280*720", ("9:16","720p"):"720*1280",
                 ("16:9","1080p"):"1920*1080", ("9:16","1080p"):"1080*1920",
                 ("1:1","720p"):"960*960", ("1:1","1080p"):"1440*1440"}
        size = sizes.get((str(ratio), str(resolution).lower()))
        if not size:
            raise ConflictError("百炼文生视频请同时选择已适配的比例和分辨率")
        result["size"] = size
    return result


def validate_image(value):
    try:
        header, encoded = value.split(",", 1)
        if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64", "data:image/bmp;base64"} or len(encoded) > 14 * 1024 * 1024:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP", "BMP"} or not all(240 <= size <= 8000 for size in image.size):
                raise ValueError()
            if "A" in image.getbands() or "transparency" in image.info:
                raise ValueError()
            image.verify()
    except (ValueError, TypeError, AttributeError, OSError, binascii.Error, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ConflictError("百炼首图请使用无透明通道的 PNG/JPEG/WebP/BMP，宽高 240–8000，当前本地校验上限 10 MB") from exc


class DashScopeVideoProvider(OpenAICompatibleProvider):
    def __init__(self, *, image_mode=False, **kwargs):
        super().__init__(**kwargs)
        self.image_mode = image_mode

    async def discover_models(self):
        raise ConflictError("百炼原生视频没有此应用支持的统一模型发现接口，请按官方文档手动添加模型；未发起收费请求")

    async def _request(self, method, path, **kwargs):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if method == "POST":
            headers["X-DashScope-Async"] = "enable"
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path, headers=headers, **kwargs)
            self._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("output"), dict):
                raise ProviderError("百炼视频响应缺少 output；请核对渠道日志，不自动重发")
            return body["output"]
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接百炼视频接口") from exc
        except ValueError as exc:
            raise ProviderError("百炼视频返回无效 JSON") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        params = video_parameters(parameters, image_mode=self.image_mode, first=bool(first_frame), last=bool(last_frame), references=len(reference_images))
        content = {"prompt": prompt}
        if negative_prompt:
            content["negative_prompt"] = negative_prompt
        if self.image_mode:
            image = first_frame or reference_images[0]
            validate_image(image)
            content["img_url"] = image
        body = await self._request("POST", "/services/aigc/video-generation/video-synthesis", json={"model": model, "input": content, "parameters": params})
        task_id = body.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("百炼未返回有效任务 ID；请核对渠道任务和账单，不自动重发")
        return VideoGenerationHandle(task_id, str(body.get("task_status") or "PENDING"))

    async def poll_video(self, handle):
        output = await self._request("GET", f"/tasks/{quote(handle.id, safe='')}")
        if output.get("task_id") != handle.id:
            raise ProviderError("百炼查询任务 ID 不匹配")
        status = output.get("task_status")
        if status == "SUCCEEDED":
            url = output.get("video_url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("百炼未返回有效 HTTPS 视频地址")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            return VideoGenerationStatus("failed", 0, "百炼任务失败、取消或已过期，请核对渠道任务记录；不重新提交")
        if status not in {"PENDING", "RUNNING"}:
            raise ProviderError("百炼返回未知任务状态，保留原任务 ID")
        return VideoGenerationStatus("processing", 0 if status == "PENDING" else 50)

    async def download_video(self, handle):
        if handle.id not in self._video_urls and (await self.poll_video(handle)).status != "succeeded":
            raise ProviderError("百炼视频尚未完成")
        return await download_stream(self, self._video_urls[handle.id])
