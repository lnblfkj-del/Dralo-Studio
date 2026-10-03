"""Explicit Kling text/image/multi-image video protocols, not Omni passthrough."""
import base64
import hashlib
import hmac
import io
import json
import math
import time
from urllib.parse import quote, urlsplit

import httpx
from app.core.outbound_http import outbound_client
from PIL import Image

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.jimeng_video import credentials
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.toapis import decode_image
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus

PATHS = {"kling_video_t2v": "text2video", "kling_video_i2v": "image2video", "kling_video_multi_image": "multi-image2video"}


def authorization(key, now=None):
    if not key.lstrip().startswith("{"):
        return "Bearer " + key
    ak, sk = credentials(key, "可灵")
    stamp = int(time.time() if now is None else now)
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(json.dumps({"iss": ak, "exp": stamp + 1800, "nbf": stamp - 5}, separators=(",", ":")).encode())
    unsigned = header + b"." + payload
    return "Bearer " + (unsigned + b"." + encode(hmac.new(sk.encode(), unsigned, hashlib.sha256).digest())).decode()


def video_parameters(protocol, parameters, *, first=False, last=False, references=0, negative_prompt=None):
    if protocol not in PATHS:
        raise ConflictError("可灵视频协议未注册")
    if protocol == "kling_video_t2v" and (first or last or references):
        raise ConflictError("可灵文生视频不能带图，请选择图生或多图协议")
    if protocol == "kling_video_i2v" and (int(first) + references > 1 or not (first or last or references)):
        raise ConflictError("可灵图生需要首图或尾图；首图最多一张，多参考图请选择多图协议")
    if protocol == "kling_video_multi_image" and (first or last or not 1 <= references <= 4):
        raise ConflictError("可灵多图协议需要 1–4 张普通参考图，不接受首尾帧")
    if negative_prompt and len(negative_prompt) > 2500:
        raise ConflictError("可灵负面提示词不能超过 2500 字符")
    unsupported = ("image", "image_tail", "image_list", "video_list", "element_list", "voice_list", "reference_urls", "image_urls",
        "content", "input", "metadata", "extra_body", "callback_url", "multi_prompt", "multi_shot", "shot_type", "camera_control",
        "static_mask", "dynamic_masks", "watermark_info", "frames", "fps", "seed", "width", "height", "size", "prompt_extend", "audio", "sound")
    if any(parameters.get(key) is not None for key in unsupported) or parameters.get("n", 1) != 1:
        raise ConflictError("可灵视频包含未开放的扩展字段；请通过节点引用素材，不能静默忽略或透传")
    result = {}
    duration = parameters.get("duration", 5)
    if type(duration) is not int or not 3 <= duration <= 15 or (protocol == "kling_video_multi_image" and duration not in (5, 10)):
        raise ConflictError("可灵时长需为 3–15 的整数秒；多图协议仅 5/10 秒，实际范围还受模型能力限制")
    result["duration"] = str(duration)
    mode = parameters.get("mode")
    resolution = parameters.get("resolution")
    defaults = (None, "", "default", "模型默认")
    if resolution not in defaults:
        mapped = {"720p": "std", "1080p": "pro", "4k": "4k"}.get(str(resolution).lower())
        if not mapped or mode not in (*defaults, mapped):
            raise ConflictError("可灵分辨率与 mode 冲突，720p/std、1080p/pro、4k/4k 必须一致")
        mode = mapped
    if mode not in defaults:
        if mode not in {"std", "pro", "4k"} or (protocol == "kling_video_multi_image" and mode == "4k"):
            raise ConflictError("当前可灵协议不支持此 mode")
        result["mode"] = mode
    ratio = parameters.get("aspect_ratio")
    if ratio not in defaults and protocol != "kling_video_i2v":
        if ratio not in {"16:9", "9:16", "1:1"}:
            raise ConflictError("可灵文生／多图画幅仅支持 16:9、9:16、1:1")
        result["aspect_ratio"] = ratio
    for key in ("generate_audio", "watermark"):
        if parameters.get(key) is not None:
            if type(parameters[key]) is not bool or (key == "generate_audio" and protocol == "kling_video_multi_image"):
                raise ConflictError(f"当前可灵协议不接受此 {key} 值")
            result["sound" if key == "generate_audio" else "watermark_info"] = ("on" if parameters[key] else "off") if key == "generate_audio" else {"enabled": parameters[key]}
    if parameters.get("cfg_scale") is not None:
        value = parameters["cfg_scale"]
        if protocol == "kling_video_multi_image" or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ConflictError("cfg_scale 需为 0–1，仅适用于支持该参数的可灵文生／图生模型")
        result["cfg_scale"] = value
    return result


def image_payload(value):
    raw, mime = decode_image(value)
    if mime not in {"image/png", "image/jpeg"} or len(raw) > 10_000_000:
        raise ConflictError("可灵参考图只支持不超过 10 MB 的 PNG/JPEG")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            w, h = image.size
            if image.format not in {"PNG", "JPEG"} or min(w, h) < 300 or not 0.4 <= w / h <= 2.5:
                raise ValueError()
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ConflictError("可灵图片必须有效，宽高至少 300，宽高比在 1:2.5 到 2.5:1 之间") from None
    return base64.b64encode(raw).decode()


class KlingVideoProvider(OpenAICompatibleProvider):
    def __init__(self, *, protocol, **kwargs):
        super().__init__(**kwargs)
        self.protocol = protocol
        self.path = "/videos/" + PATHS[protocol]
        authorization(self.api_key)

    async def discover_models(self):
        raise ConflictError("可灵原生视频请按官方能力表手动添加 model_name，当前不执行模型发现或收费测试")

    async def _request(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path, headers={"Authorization": authorization(self.api_key)}, **kwargs)
            self._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError()
            if body.get("code") != 0:
                code = body.get("code")
                raise ProviderError(f"可灵接口失败（代码 {code if type(code) is int else '未知'}），请核对渠道任务日志")
            if not isinstance(body.get("data"), dict):
                raise ValueError()
            return body["data"]
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接可灵原生视频接口") from exc
        except ValueError as exc:
            raise ProviderError("可灵返回无效任务响应") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        params = video_parameters(self.protocol, parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images), negative_prompt=negative_prompt)
        if not prompt.strip() or len(prompt) > 2500:
            raise ConflictError("可灵提示词不能为空且不能超过 2500 字符")
        body = {"model_name": model, "prompt": prompt, **params}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if self.protocol == "kling_video_i2v":
            first = first_frame or (reference_images[0] if reference_images else None)
            if first:
                body["image"] = image_payload(first)
            if last_frame:
                body["image_tail"] = image_payload(last_frame)
        elif self.protocol == "kling_video_multi_image":
            body["image_list"] = [{"image": image_payload(value)} for value in reference_images]
        data = await self._request("POST", self.path, json=body)
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("可灵未返回有效任务 ID，请核对渠道任务和账单，不自动重发")
        return VideoGenerationHandle(task_id, "submitted")

    async def poll_video(self, handle):
        body = await self._request("GET", self.path + "/" + quote(handle.id, safe=""))
        if body.get("task_id") != handle.id:
            raise ProviderError("可灵查询任务 ID 不匹配，保留原任务 ID")
        status = body.get("task_status")
        if status == "failed":
            return VideoGenerationStatus("failed", 0, "可灵任务失败，请凭任务 ID 核对渠道错误详情")
        if status == "succeed":
            result = body.get("task_result")
            videos = result.get("videos") if isinstance(result, dict) else None
            if not isinstance(videos, list) or len(videos) != 1 or not isinstance(videos[0], dict):
                raise ProviderError("可灵单任务未返回唯一视频结果，保留任务 ID")
            url = videos[0].get("url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("可灵未返回有效 HTTPS 视频地址")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status not in {"submitted", "processing"}:
            raise ProviderError("可灵返回未知状态，保留原任务 ID，不重新提交")
        return VideoGenerationStatus("processing", 0 if status == "submitted" else 50)

    async def download_video(self, handle):
        if handle.id not in self._video_urls and (await self.poll_video(handle)).status != "succeeded":
            raise ProviderError("可灵视频尚未完成")
        return await download_stream(self, self._video_urls[handle.id])
