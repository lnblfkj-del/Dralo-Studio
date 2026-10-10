"""MEAICC JSON /v1/videos contract with explicit text and image modes."""
from urllib.parse import quote, urlsplit

import httpx

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.core.outbound_http import outbound_client
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus

RESOLUTIONS = {"mx-h3": "768p", "sd-2-c4": "720p"}
RATIOS = {"16:9", "9:16", "1:1"}
CONFIG_FIELDS = {"durations", "resolutions", "aspect_ratios", "max_reference_images",
    "supported_video_input_modes", "supports_first_frame", "supports_last_frame",
    "max_shots_per_segment", "supports_audio", "supports_dialogue", "native_audio_capability"}
CONTEXT_FIELDS = {"asset_context", "canvas_request_id", "canvas_request_digest", "source_node_key",
    "execution", "video_compilation", "director_shot_package", "video_input_contract",
    "video_input_confirmations", "audio_policy", "audio_contract", "voice_guidance"}


def video_parameters(parameters, *, first=False, last=False, references=0, negative_prompt=None, image_mode=False):
    if image_mode:
        if type(references) is not int or references < 0 or not 1 <= int(first) + int(last) + references <= 9:
            raise ConflictError("MEAICC 图生需要 1–9 张图片，首尾帧也占用图片名额")
        if last and not first:
            raise ConflictError("MEAICC 尾帧必须同时提供首帧")
        if (first or last) and references:
            raise ConflictError("MEAICC 首尾帧与普通参考图暂不混用，请明确选择一种输入方式")
    elif first or last or references:
        raise ConflictError("MEAICC 文生协议不接收图片，请选择独立的图生／参考图协议")
    if negative_prompt:
        raise ConflictError("MEAICC 未定义独立负向提示词字段，请在正文描述约束")
    if not image_mode and any(parameters.get(key) for key in ("references", "first_frame_media_id", "last_frame_media_id", "reference_media_ids")):
        raise ConflictError("MEAICC 当前文生模式不接收参考素材")
    allowed = {"duration", "aspect_ratio", "resolution"}
    # The application merges capability metadata into defaults; never send it upstream.
    local_fields = CONFIG_FIELDS | CONTEXT_FIELDS | {"references", "first_frame_media_id", "last_frame_media_id", "reference_media_ids"}
    if any(value is not None for key, value in parameters.items() if key not in allowed | local_fields):
        raise ConflictError("MEAICC 当前只支持时长、比例和分辨率；不支持的参数不能静默忽略")
    duration = parameters.get("duration")
    if type(duration) not in (int, float) or not 4 <= duration <= 15 or int(duration) != duration:
        raise ConflictError("MEAICC 时长必须为 4–15 秒的整数")
    if not isinstance(parameters.get("aspect_ratio"), str) or parameters["aspect_ratio"] not in RATIOS:
        raise ConflictError("MEAICC 当前开放 16:9、9:16、1:1 比例")
    resolution = str(parameters.get("resolution", "")).lower()
    if resolution not in set(RESOLUTIONS.values()):
        raise ConflictError("MEAICC 请明确选择模型支持的 720p 或 768p 分辨率")
    return {"duration": int(duration), "ratio": parameters["aspect_ratio"], "resolution": resolution}


class MeaiccVideoProvider(OpenAICompatibleProvider):
    poll_interval_seconds = 20

    def __init__(self, *, image_mode=False, **kwargs):
        super().__init__(**kwargs)
        self.image_mode = image_mode

    async def _request(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path,
                    headers={"Authorization": f"Bearer {self.api_key}"}, **kwargs)
            self._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError("MEAICC 返回无效任务结构；不自动重发")
            return body["data"] if isinstance(body.get("data"), dict) else body
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("MEAICC 视频接口连接失败；请保留原任务，不自动重发") from exc
        except ValueError as exc:
            raise ProviderError("MEAICC 视频接口未返回有效 JSON；不自动重发") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        params = video_parameters(parameters, first=bool(first_frame), last=bool(last_frame),
                                  references=len(reference_images), negative_prompt=negative_prompt,
                                  image_mode=self.image_mode)
        if model not in RESOLUTIONS or params["resolution"] != RESOLUTIONS[model]:
            raise ConflictError("MEAICC 当前只适配 mx-h3（768p）和 sd-2-c4（720p）")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConflictError("视频提示词不能为空")
        media = []
        if self.image_mode:
            from app.providers.meaicc_media import decode_image, upload_image
            images = ([("first_frame", first_frame)] if first_frame else [])
            images += [("reference_image", value) for value in reference_images]
            images += [("last_frame", last_frame)] if last_frame else []
            # Validate every image before transmitting even the first one.
            decoded = [(role, *decode_image(value)) for role, value in images]
            urls = {}
            for role, raw, mime in decoded:
                if raw not in urls:
                    urls[raw] = await upload_image(self, raw, mime)
                media.append({"type": role, "url": urls[raw]})
        body = await self._request("POST", "/videos", json={"model": model,
            "input": {"prompt": prompt, "media": media}, "parameters": params})
        if str(body.get("status", "")).lower().startswith(("failed", "error")):
            raise ProviderError("MEAICC 拒绝创建视频任务，请查看渠道记录；不自动重发")
        task_id = body.get("task_id") or body.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("MEAICC 未返回有效任务编号；请核对渠道账单，不自动重发")
        return VideoGenerationHandle(task_id, str(body.get("status") or "PENDING"))

    async def poll_video(self, handle):
        body = await self._request("GET", f"/videos/{quote(handle.id, safe='')}")
        returned_id = body.get("task_id") or body.get("id")
        if returned_id and returned_id != handle.id:
            raise ProviderError("MEAICC 查询返回的任务编号不匹配")
        status = str(body.get("status", "")).strip().upper()
        if status == "SUCCEEDED":
            url = body.get("object")
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
            except ValueError as exc:
                raise ProviderError("MEAICC 返回的视频地址格式无效") from exc
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("MEAICC 未返回有效 HTTPS 视频下载地址")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status.startswith(("FAILED", "ERROR")) or status in {"CANCELED", "CANCELLED"}:
            return VideoGenerationStatus("failed", 0, "MEAICC 视频生成失败，请核对渠道任务记录；未自动重新提交")
        if status not in {"PENDING", "RUNNING"}:
            raise ProviderError("MEAICC 返回未知任务状态；保留原任务编号")
        value = body.get("progress")
        progress = 0 if status == "PENDING" else 50
        if type(value) in (int, float) and 0 <= value <= 100:
            progress = min(99, int(value))
        return VideoGenerationStatus("processing", progress)

    async def download_video(self, handle):
        if handle.id not in self._video_urls and (await self.poll_video(handle)).status != "succeeded":
            raise ProviderError("MEAICC 视频尚未完成")
        media = await download_stream(self, self._video_urls[handle.id], content_types={
            "video/mp4", "video/webm", "application/octet-stream", "binary/octet-stream"})
        if not (len(media) >= 12 and media[4:8] == b"ftyp" or media.startswith(b"\x1a\x45\xdf\xa3")):
            raise ProviderError("MEAICC 下载内容不是可识别的视频容器；保留原任务，不重新生成")
        return media
