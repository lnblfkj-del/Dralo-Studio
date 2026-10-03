"""Jimeng 3.0 720p first/last-frame native Visual API contract."""
import base64
import hashlib
import hmac
import io
import json
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from app.core.outbound_http import outbound_client
from PIL import Image

from app.core.errors import ConflictError, ProviderAuthError, ProviderError, RateLimitError, TimeoutError_
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.toapis import decode_image
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus

MODEL = "jimeng_i2v_first_tail_v30"
SPECS = {
    "jimeng_video_first_last": (MODEL, "first_last", "720p"),
    "jimeng_video_t2v": ("jimeng_t2v_v30", "text", "720p"),
    "jimeng_video_pro": ("jimeng_ti2v_v30_pro", "optional_first", "1080p"),
}


def credentials(value, label="即梦"):
    try:
        pair = json.loads(value)
        if not isinstance(pair, dict) or set(pair) != {"access_key", "secret_key"}:
            raise ValueError()
        if any(not isinstance(v, str) or not v.strip() or len(v) > 1024 or any(c.isspace() for c in v) for v in pair.values()):
            raise ValueError()
        return pair["access_key"], pair["secret_key"]
    except (TypeError, ValueError):
        raise ConflictError(label + '双密钥凭证需填写 JSON：{"access_key":"AK","secret_key":"SK"}') from None


def signed_headers(base_url, action, body, access_key, secret_key, now=None):
    """Volcengine V4, fixed Visual API region/service; no credential forwarding."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(body).hexdigest()
    host = urlsplit(base_url).hostname
    headers = {"content-type": "application/json", "host": host, "x-content-sha256": digest, "x-date": stamp}
    names = ";".join(sorted(headers))
    query = urlencode({"Action": action, "Version": "2022-08-31"})
    canonical = "\n".join(["POST", "/", query, "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), names, digest])
    scope = f"{stamp[:8]}/cn-north-1/cv/request"
    signing_key = secret_key.encode()
    for item in (stamp[:8], "cn-north-1", "cv", "request"):
        signing_key = hmac.new(signing_key, item.encode(), hashlib.sha256).digest()
    message = f"HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signature = hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={names}, Signature={signature}"
    return headers, query


def video_parameters(parameters, *, first=False, last=False, references=0, negative_prompt=None, protocol="jimeng_video_first_last"):
    _, mode, resolution = SPECS[protocol]
    if mode == "first_last" and (not first or not last or references):
        raise ConflictError("即梦 3.0 首尾帧需要明确的一张首帧和一张尾帧，不接受普通参考图")
    if mode == "text" and (first or last or references):
        raise ConflictError("即梦文生协议不接收图片；请选择 Pro 或首尾帧协议")
    if mode == "optional_first" and (last or int(first) + references > 1):
        raise ConflictError("即梦 Pro 仅接收文本或一张首图，不支持尾帧及多图")
    if negative_prompt:
        raise ConflictError("即梦首尾帧没有独立负面提示词映射，请合并到提示词")
    if parameters.get("duration", 5) not in (5, 10) or type(parameters.get("duration", 5)) is not int:
        raise ConflictError("即梦 3.0 首尾帧仅支持 5 秒或 10 秒")
    if parameters.get("resolution") not in (None, "", "default", "模型默认", resolution):
        raise ConflictError(f"当前即梦协议固定为 {resolution}")
    if parameters.get("n", 1) != 1:
        raise ConflictError("即梦每个任务仅生成一个视频，请使用批量任务")
    unsupported = ("content", "input", "image", "images", "image_urls", "binary_data_base64", "reference_urls", "req_key", "req_json",
                   "metadata", "audio", "generate_audio", "watermark", "prompt_extend", "shot_type", "frames", "fps", "size", "width", "height")
    if any(parameters.get(key) is not None for key in unsupported):
        raise ConflictError("即梦首尾帧扩展参数尚未适配，请使用时长和 seed；媒体必须通过节点引用")
    result = {"frames": parameters.get("duration", 5) * 24 + 1}
    if mode == "text" or (mode == "optional_first" and not (first or references)):
        ratio = parameters.get("aspect_ratio", "16:9")
        if ratio not in (None, "", "default", "模型默认"):
            if ratio not in {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}:
                raise ConflictError("即梦文生画幅不受支持")
            result["aspect_ratio"] = ratio
    seed = parameters.get("seed", -1)
    if type(seed) is not int or not -1 <= seed <= 2147483647:
        raise ConflictError("即梦 seed 必须为 -1 到 2147483647 的整数")
    result["seed"] = seed
    return result


def image_payload(value):
    raw, mime = decode_image(value)
    if mime not in {"image/png", "image/jpeg"} or len(raw) > 4_700_000:
        raise ConflictError("即梦首尾帧请使用不超过 4.7 MB 的 PNG/JPEG")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if image.format not in {"PNG", "JPEG"} or min(width, height) < 320 or max(width, height) > 4096 or max(width, height) / min(width, height) > 3:
                raise ValueError()
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ConflictError("即梦图片宽高需为 320–4096，长短边比例不超过 3，且文件内容有效") from None
    return base64.b64encode(raw).decode(), (width, height)


class JimengVideoProvider(OpenAICompatibleProvider):
    def __init__(self, *, protocol="jimeng_video_first_last", **kwargs):
        super().__init__(**kwargs)
        self.protocol = protocol
        self.req_key = SPECS[protocol][0]
        self.access_key, self.secret_key = credentials(self.api_key)

    async def discover_models(self):
        raise ConflictError(f"当前即梦原生入口请手动添加 {self.req_key}，不支持统一模型发现；未发起收费生成")

    async def _request(self, action, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 10_000_000:
            raise ConflictError("即梦请求体超过本应用 10 MB 安全上限，请压缩首尾帧图片")
        headers, query = signed_headers(self.base_url, action, body, self.access_key, self.secret_key)
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.post(self.base_url + "/?" + query, content=body, headers=headers)
            self._raise_for_provider_error(response)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            code = result.get("code")
            if code in (50429, 50430):
                raise RateLimitError()
            if code == 50400:
                raise ProviderAuthError("即梦 AK/SK 鉴权失败，请检查凭证和本机时间")
            if code != 10000:
                suffix = str(code) if type(code) is int else "未知"
                raise ProviderError(f"即梦接口失败（代码 {suffix}），请核对渠道日志；不自动重新提交")
            if not isinstance(result.get("data"), dict):
                raise ValueError()
            return result["data"]
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接即梦原生视频接口") from exc
        except ValueError as exc:
            raise ProviderError("即梦响应不是有效任务对象") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        if model != self.req_key:
            raise ConflictError(f"当前即梦协议仅适配 {self.req_key}，不能推测其他版本参数")
        params = video_parameters(parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images), negative_prompt=negative_prompt, protocol=self.protocol)
        images = [image_payload(value) for value in [first_frame, last_frame, *reference_images] if value]
        if len(images) == 2 and images[0][1][0] * images[1][1][1] != images[1][1][0] * images[0][1][1]:
            raise ConflictError("即梦首帧和尾帧的画幅比例必须相同")
        if not prompt.strip() or len(prompt) > 800:
            raise ConflictError("即梦提示词不能为空且不能超过 800 字符")
        payload = {"req_key": self.req_key, "prompt": prompt, **params}
        if images:
            payload["binary_data_base64"] = [item[0] for item in images]
        body = await self._request("CVSync2AsyncSubmitTask", payload)
        task_id = body.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("即梦未返回有效任务 ID，请核对渠道任务与账单；不自动重发")
        return VideoGenerationHandle(task_id, "in_queue")

    async def poll_video(self, handle):
        body = await self._request("CVSync2AsyncGetResult", {"req_key": self.req_key, "task_id": handle.id})
        status = body.get("status")
        if status in {"expired", "not_found"}:
            return VideoGenerationStatus("failed", 0, "即梦任务不存在或已过期，请凭原任务 ID 核对渠道记录")
        if status == "done":
            url = body.get("video_url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderError("即梦任务完成但未返回有效 HTTPS 视频地址，请核对渠道记录")
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status not in {"in_queue", "generating"}:
            raise ProviderError("即梦返回未知任务状态，保留原任务 ID")
        return VideoGenerationStatus("processing", 0 if status == "in_queue" else 50)

    async def download_video(self, handle):
        if handle.id not in self._video_urls and (await self.poll_video(handle)).status != "succeeded":
            raise ProviderError("即梦视频尚未完成")
        return await download_stream(self, self._video_urls[handle.id])
