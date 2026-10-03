"""New API generic video v1 contract. Not the separate /videos Sora contract."""
import math
from urllib.parse import quote, urlsplit

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.toapis import decode_image
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus


async def download_stream(adapter, url, headers=None):
    try:
        async with outbound_client(timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as response:
                adapter._raise_for_provider_error(response)
                if response.headers.get("content-type", "").split(";")[0] not in {"video/mp4", "video/webm", "application/octet-stream"}:
                    raise ProviderError("视频内容接口未返回视频文件")
                chunks, total = [], 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > 500 * 1024 * 1024:
                        raise ProviderError("视频生成结果超过 500 MB")
                    chunks.append(chunk)
        if not total:
            raise ProviderError("视频内容为空")
        return b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise TimeoutError_() from exc
    except httpx.HTTPError as exc:
        raise ProviderError("无法下载视频内容") from exc


def video_parameters(parameters, *, first=False, last=False, references=0, sora=False):
    if last or int(first) + references > 1:
        raise ConflictError("New API 通用视频接口只接收单张图片；不支持尾帧或多参考输入")
    if any(parameters.get(key) for key in ("image", "images", "image_urls", "metadata", "reference_urls", "input_reference")):
        raise ConflictError("请通过画布素材引用传入图片，不允许扩展参数绕过引用权限")
    result = {key: parameters[key] for key in ("duration", "width", "height", "fps", "seed") if parameters.get(key) not in (None, "", "default")}
    for key, value in result.items():
        if type(value) not in (int, float) or not math.isfinite(value) or (key != "seed" and value <= 0):
            raise ConflictError(f"New API {key} 必须是有效数字")
        if key != "duration" and type(value) is not int:
            raise ConflictError(f"New API {key} 必须为整数")
    if ("width" in result) != ("height" in result):
        raise ConflictError("视频宽度和高度必须一起设置")
    ratio = parameters.get("aspect_ratio")
    resolution = parameters.get("resolution")
    if ratio not in (None, "", "default", "模型默认") or resolution not in (None, "", "default", "模型默认"):
        # Never guess the aspect ratio or silently discard canvas size settings.
        sizes = (
            {("16:9", "720p"): (1280, 720), ("9:16", "720p"): (720, 1280),
             ("16:9", "1024p"): (1792, 1024), ("9:16", "1024p"): (1024, 1792)}
            if sora else
            {("16:9", "480p"):(854,480), ("9:16","480p"):(480,854),
             ("16:9","720p"):(1280,720), ("9:16","720p"):(720,1280),
             ("16:9","1080p"):(1920,1080), ("9:16","1080p"):(1080,1920),
             ("1:1","480p"):(480,480), ("1:1","720p"):(720,720), ("1:1","1080p"):(1080,1080)}
        )
        size = sizes.get((str(ratio), str(resolution)))
        if not size:
            raise ConflictError("New API 请同时选择支持的比例与分辨率，或使用模型默认并明确配置 width/height")
        if "width" in result and (result["width"], result["height"]) != size:
            raise ConflictError("视频宽高与画布比例／分辨率冲突")
        result.update(width=size[0], height=size[1])
    if parameters.get("n", 1) != 1:
        raise ConflictError("每个画布视频任务只接收一个生成结果，请使用批量任务")
    for key in ("audio", "generate_audio", "shot_type", "watermark", "prompt_extend"):
        if parameters.get(key) is not None:
            raise ConflictError(f"New API 通用协议尚未定义 {key} 映射，不能静默忽略")
    if sora and any(key in result for key in ("fps", "seed")):
        raise ConflictError("Sora 兼容视频基础协议不支持 fps/seed 参数")
    if sora and "duration" in result and (
        type(result["duration"]) is not int or result["duration"] not in {4, 8, 12}
    ):
        raise ConflictError("Sora /videos 只支持 4、8、12 秒")
    if any(parameters.get(key) not in (None, "", "default") for key in ("size", "seconds")):
        raise ConflictError("请通过 duration 及画布比例／分辨率设置视频参数，不要重复配置 size/seconds")
    return result


class NewAPIProvider(OpenAICompatibleProvider):
    async def _request(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers={"Authorization": f"Bearer {self.api_key}"}, **kwargs)
            self._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("not an object")
            return body
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 New API 视频接口") from exc
        except ValueError as exc:
            raise ProviderError("New API 视频响应不是有效 JSON 对象") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        payload = {"model": model, "prompt": prompt,
                   **video_parameters(parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images))}
        image = first_frame or (reference_images[0] if reference_images else None)
        if image:
            decode_image(image)  # Limit and validate before any network request.
            payload["image"] = image
        if negative_prompt:
            payload["metadata"] = {"negative_prompt": negative_prompt}
        body = await self._request("POST", "/video/generations", json=payload)
        task_id = body.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("New API 未返回有效任务 ID；不要自动重发，请核对渠道任务和账单")
        return VideoGenerationHandle(id=task_id, status=str(body.get("status") or "queued"))

    async def poll_video(self, handle):
        body = await self._request("GET", f"/video/generations/{quote(handle.id, safe='')}")
        if body.get("task_id") != handle.id:
            raise ProviderError("New API 查询返回任务 ID 不匹配")
        status = body.get("status")
        if status == "completed":
            url = body.get("url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("New API 完成任务未返回有效 HTTPS 下载地址")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus(status="succeeded", progress=100)
        if status == "failed":
            error = body.get("error")
            # Channel raw error bodies can contain signed URLs or echoed inputs.
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f"（代码 {code}）" if type(code) is int else ""
            return VideoGenerationStatus(status="failed", progress=0, error=f"New API 视频生成失败{suffix}，请在渠道任务记录核对原因")
        if status not in {"queued", "in_progress"}:
            raise ProviderError("New API 返回未知视频任务状态，保留任务 ID，不重新提交")
        return VideoGenerationStatus(status="processing", progress=0 if status == "queued" else 50)

    async def download_video(self, handle):
        if handle.id not in self._video_urls:
            status = await self.poll_video(handle)
            if status.status != "succeeded":
                raise ProviderError("视频尚未完成，不能下载")
        return await download_stream(self, self._video_urls[handle.id])


class SoraCompatibleProvider(NewAPIProvider):
    """Explicit multipart /videos protocol; never inferred from a model name."""
    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        params = video_parameters(parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images), sora=True)
        if negative_prompt:
            raise ConflictError("Sora 兼容基础协议没有独立负面提示词字段，请合并到提示词")
        fields = {"model": (None, model), "prompt": (None, prompt)}
        if "duration" in params:
            if type(params["duration"]) is not int:
                raise ConflictError("Sora 兼容视频时长必须为整数秒")
            fields["seconds"] = (None, str(params["duration"]))
        if "width" in params:
            fields["size"] = (None, f"{params['width']}x{params['height']}")
        image = first_frame or (reference_images[0] if reference_images else None)
        if image:
            data, mime = decode_image(image)
            if mime == "image/gif":
                raise ConflictError("Sora 兼容首图请使用 PNG、JPEG 或 WebP")
            fields["input_reference"] = ("reference." + mime.split("/")[1], data, mime)
        body = await self._request("POST", "/videos", files=fields)
        task_id = body.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("视频接口未返回有效任务 ID；不要自动重发，请核对渠道任务和账单")
        return VideoGenerationHandle(id=task_id, status=str(body.get("status") or "queued"))

    async def poll_video(self, handle):
        body = await self._request("GET", f"/videos/{quote(handle.id, safe='')}")
        if body.get("id") != handle.id:
            raise ProviderError("视频查询返回任务 ID 不匹配")
        if body.get("status") == "completed":
            return VideoGenerationStatus(status="succeeded", progress=100)
        if body.get("status") == "failed":
            return VideoGenerationStatus(status="failed", error="视频渠道生成失败，请在渠道任务记录核对原因")
        if body.get("status") not in {"queued", "in_progress"}:
            raise ProviderError("视频渠道返回未知任务状态，保留任务 ID，不重新提交")
        return VideoGenerationStatus(status="processing", progress=50)

    async def download_video(self, handle):
        # /content is authenticated on the exact configured origin. Do not
        # follow redirects with the channel credential or guess a CDN URL.
        return await download_stream(self, f"{self.base_url}/videos/{quote(handle.id, safe='')}/content",
                                     {"Authorization": f"Bearer {self.api_key}"})
