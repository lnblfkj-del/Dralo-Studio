"""Ark native text-to-video task contract; media modes are deliberately separate."""
from urllib.parse import quote, urlsplit
import io

import httpx
from app.core.outbound_http import outbound_client
from PIL import Image

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus
from app.providers.toapis import decode_image


def video_parameters(parameters, *, first=False, last=False, references=0, negative_prompt=None, image_mode=False):
    if not image_mode and (first or last or references):
        raise ConflictError("方舟原生文生视频不接收参考图；请选择方舟图生／首尾帧／多参考图协议，不会忽略图片提交")
    if image_mode and (not (first or references) or (last and not first) or (references and (first or last)) or references > 9):
        raise ConflictError("方舟图生请选择首帧／首尾帧，或 1–9 张普通参考图；两种模式不能混用，数量仍受模型配置限制")
    if negative_prompt:
        raise ConflictError("方舟原生视频没有已适配的独立负面提示词字段，请合并到提示词")
    # These fields must not bypass authenticated media resolution or alter billing modes.
    unsupported = ("content", "input", "image", "images", "image_urls", "reference_urls",
                   "video_url", "audio_url", "metadata", "extra_body", "callback_url",
                   "frames", "fps", "width", "height", "size", "ratio", "audio",
                   "shot_type", "prompt_extend", "draft", "return_last_frame", "tools",
                   "service_tier", "priority", "execution_expires_after", "output_format")
    if any(parameters.get(key) is not None for key in unsupported):
        raise ConflictError("方舟文生视频扩展参数包含尚未适配的字段；请使用时长、比例、分辨率及已开放的布尔选项")
    if parameters.get("n", 1) != 1:
        raise ConflictError("每个视频任务只接收一个结果，请使用批量任务")
    result = {}
    for key in ("duration", "seed"):
        value = parameters.get(key)
        if value is None:
            continue
        if type(value) is not int or (key == "duration" and not 1 <= value <= 30) or (key == "seed" and not -1 <= value <= 2147483647):
            raise ConflictError(f"方舟 {key} 超出本应用基础校验范围，请按模型能力配置")
        result[key] = value
    for key in ("watermark", "camera_fixed", "generate_audio"):
        if parameters.get(key) is not None:
            if type(parameters[key]) is not bool:
                raise ConflictError(f"{key} 必须为布尔值")
            result[key] = parameters[key]
    for source, target, values in (
        ("resolution", "resolution", {"480p", "720p", "1080p"}),
        ("aspect_ratio", "ratio", {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"} | ({"adaptive"} if image_mode else set())),
    ):
        value = parameters.get(source)
        if value in (None, "", "default", "模型默认"):
            continue
        if not isinstance(value, str) or value.lower() not in values:
            raise ConflictError(f"方舟 {source} 尚未开放此值，请按模型能力选择")
        result[target] = value.lower()
    return result


class ArkVideoProvider(OpenAICompatibleProvider):
    def __init__(self, *, image_mode=False, **kwargs):
        super().__init__(**kwargs)
        self.image_mode = image_mode

    async def discover_models(self):
        raise ConflictError("方舟原生视频请手动添加官方完整模型 ID 或推理接入点 ID；当前入口不执行模型发现或收费测试")

    async def _request(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path, headers={"Authorization": f"Bearer {self.api_key}"}, **kwargs)
            self._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("not an object")
            return body
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接方舟视频接口") from exc
        except ValueError as exc:
            raise ProviderError("方舟视频返回无效 JSON 对象") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        params = video_parameters(parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images), negative_prompt=negative_prompt, image_mode=self.image_mode)
        content = [{"type": "text", "text": prompt}]
        inputs = [(first_frame, "first_frame"), (last_frame, "last_frame"), *((image, "reference_image") for image in reference_images)]
        total = 0
        for value, role in inputs:
            if not value:
                continue
            raw, mime = decode_image(value)
            if mime not in {"image/png", "image/jpeg"} or len(raw) > 10_000_000:
                raise ConflictError("方舟图生当前本地开放 PNG/JPEG，每张不超过 10 MB")
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    width, height = image.size
                    if image.format not in {"PNG", "JPEG"} or not 300 <= min(width, height) <= max(width, height) <= 6000 or not 0.4 <= width / height <= 2.5:
                        raise ValueError()
                    image.verify()
            except (OSError, ValueError, Image.DecompressionBombError):
                raise ConflictError("方舟参考图请使用有效图片；本地安全范围为宽高 300–6000、宽高比 0.4–2.5") from None
            total += len(value)
            if total > 32_000_000:
                raise ConflictError("方舟参考图编码合计超过本地 32 MB 安全上限，请减少或压缩图片")
            content.append({"type": "image_url", "image_url": {"url": value}, "role": role})
        body = await self._request("POST", "/contents/generations/tasks", json={
            "model": model, "content": content, **params,
        })
        task_id = body.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("方舟未返回有效任务 ID；请核对渠道任务和账单，不自动重发")
        return VideoGenerationHandle(task_id, "queued")

    async def poll_video(self, handle):
        body = await self._request("GET", f"/contents/generations/tasks/{quote(handle.id, safe='')}")
        if body.get("id") != handle.id:
            raise ProviderError("方舟查询任务 ID 不匹配，保留原任务 ID")
        status = body.get("status")
        if status == "succeeded":
            content = body.get("content")
            url = content.get("video_url") if isinstance(content, dict) else None
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("方舟未返回有效 HTTPS 视频下载地址")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status in {"failed", "cancelled", "expired"}:
            # Never echo raw provider errors: they may contain prompts or signed URLs.
            return VideoGenerationStatus("failed", 0, "方舟任务失败、取消或过期，请凭任务 ID 核对渠道错误详情；不重新提交")
        if status not in {"queued", "running"}:
            raise ProviderError("方舟返回未知视频状态，保留原任务 ID，不重新提交")
        return VideoGenerationStatus("processing", 0 if status == "queued" else 50)

    async def download_video(self, handle):
        if handle.id not in self._video_urls and (await self.poll_video(handle)).status != "succeeded":
            raise ProviderError("方舟视频尚未完成")
        return await download_stream(self, self._video_urls[handle.id])
