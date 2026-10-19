"""Data-only JSON task transport. No expressions, code loading, redirects or POST retries."""

import json
import re
import ipaddress
from hashlib import sha256
from urllib.parse import quote, urlsplit

import httpx

from app.core.errors import AppError, ConflictError, ProviderError, TimeoutError_
from app.core.outbound_http import PublicTransport, outbound_client
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.toapis import decode_image
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus
from app.schemas.video_connection import VideoConnection


def lookup(value, path):
    try:
        for part in path.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise ProviderError("视频接口返回中缺少已配置的字段，请核对返回字段路径") from None


def sources(value):
    if isinstance(value, str):
        return {value[1:]} if value.startswith("$") else set()
    if isinstance(value, (dict, list)):
        return set().union(*(sources(item) for item in (value.values() if isinstance(value, dict) else value)), set())
    return set()


def validate_mapping(connection, parameters, *, first=False, last=False, references=0, negative_prompt=None):
    mapped = sources(connection.request)
    if not {"model", "prompt"} <= mapped:
        raise ConflictError("自定义接法必须映射 $model 和 $prompt，不能改为隐藏的固定模型或提示词")
    for field in ("audio", "generate_audio"):
        if parameters.get(field) is not None and type(parameters[field]) is not bool:
            raise ConflictError(f"{field} 必须为布尔值")
    if parameters.get("seed") is not None and type(parameters["seed"]) is not int:
        raise ConflictError("seed 必须为整数")
    for name, required in (("first_frame", first), ("last_frame", last),
                           ("reference_images", references), ("negative_prompt", bool(negative_prompt))):
        if required and name not in mapped:
            raise ConflictError(f"自定义接法没有映射 {name}，不能丢弃已绑定素材或生成要求")
    for name in ("duration", "resolution", "aspect_ratio", "audio", "generate_audio", "seed"):
        if parameters.get(name) is not None and name not in mapped and f"param.{name}" not in mapped:
            raise ConflictError(f"自定义接法没有映射生成参数 {name}")
    def contains_url(value):
        if isinstance(value, str):
            return value.lower().startswith(("http:", "https:", "data:", "file:"))
        if isinstance(value, (list, dict)):
            return any(contains_url(item) for item in (value.values() if isinstance(value, dict) else value))
        return False
    if any(contains_url(parameters.get(source[6:])) for source in mapped if source.startswith("param.")):
        raise ConflictError("图片和音视频地址不能通过附加参数绕过素材引用权限")
    return parameters


def render(value, context):
    if isinstance(value, str) and value.startswith("$"):
        token = value[1:]
        return context["parameters"].get(token[6:]) if token.startswith("param.") else context.get(token)
    if isinstance(value, dict):
        result = {key: render(child, context) for key, child in value.items()}
        return {key: child for key, child in result.items() if child is not None}
    if isinstance(value, list):
        return [render(child, context) for child in value]
    return value


def safe_public_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    if (not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or len(value) > 8192):
        raise ProviderError("视频结果必须为不含账号密码的 HTTPS 公网地址")
    if parsed.hostname == "localhost" or parsed.hostname.endswith((".localhost", ".local")):
        raise ProviderError("接法链接或视频结果不能指向本地网络")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ProviderError("接法链接或视频结果不能指向非公网地址")
    return value


class ConfiguredVideoProvider(OpenAICompatibleProvider):
    def __init__(self, *, connection, **options):
        super().__init__(**options)
        self.connection = VideoConnection.model_validate(connection)
        self.poll_interval_seconds = self.connection.poll_seconds

    def headers(self):
        headers = ({} if self.connection.authentication == "none" else
            {"Authorization": f"Bearer {self.api_key}"} if self.connection.authentication == "bearer"
            else {"x-api-key": self.api_key})
        adapter = self.connection.service_adapter
        if adapter:
            headers.update({"X-Video-Adapter-Id": adapter.id, "X-Video-Adapter-Version": adapter.version})
        return headers

    def check_release(self, response):
        adapter = self.connection.service_adapter
        if adapter and (response.headers.get("X-Video-Adapter-Id") != adapter.id or
                        response.headers.get("X-Video-Adapter-Version") != adapter.version):
            raise ProviderError("适配服务未返回原版本确认，已停止；不会切换接法或重新生成")

    async def _json(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                async with client.stream(method, self.base_url + path, headers=self.headers(), **kwargs) as response:
                    if not 200 <= response.status_code < 300:
                        raise ProviderError(f"视频接法请求失败（HTTP {response.status_code}）；不会自动重发生成请求")
                    self.check_release(response)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ProviderError("视频接口返回超过大小限制")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("视频接法连接失败；提交结果可能不明，请核对原任务，不自动重发") from exc
        except (ValueError, TypeError) as exc:
            raise ProviderError("视频接口未返回有效 JSON 对象；不会自动重发") from exc

    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        validate_mapping(self.connection, parameters, first=bool(first_frame), last=bool(last_frame),
                         references=len(reference_images), negative_prompt=negative_prompt)
        if self.connection.service_adapter:
            adapter = self.connection.service_adapter
            try:
                ready = await self._json("GET", f"/video-adapter/versions/{adapter.id}/{adapter.version}")
                if ready.get("ready") is not True:
                    raise ProviderError("适配服务版本尚未就绪")
            except AppError as exc:
                raise ProviderError("适配服务未确认指定版本，未调用视频生成接口", details={
                    "video_transport": "configured.v1", "transport_stage": "adapter_handshake",
                    "video_generation_not_submitted": True,
                }) from exc
        images = [image for image in (first_frame, last_frame, *reference_images) if image]
        decoded, total = {}, 0
        for image in images:
            if image in decoded:
                continue
            raw, mime = decode_image(image)
            total += len(raw)
            if total > 32 * 1024 * 1024:
                raise ConflictError("视频参考图片合计超过32MB，请压缩后重试")
            decoded[image] = raw, mime
        uploaded = {}
        if self.connection.upload_path:
            try:
                for image, (raw, mime) in decoded.items():
                    suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
                    result = await self._json("POST", self.connection.upload_path,
                                              files={self.connection.upload_field: (f"reference.{suffix}", raw, mime)})
                    uploaded[image] = safe_public_url(lookup(result, self.connection.upload_url_path))
            except AppError as exc:
                raise ProviderError("参考图上传未完成，未调用视频生成接口", details={
                    "video_transport": "configured.v1", "transport_stage": "media_upload",
                    "video_generation_not_submitted": True,
                }) from exc
        mapped = lambda image: uploaded.get(image, image)
        context = {**parameters, "model": model, "prompt": prompt, "negative_prompt": negative_prompt,
                   "parameters": parameters, "first_frame": mapped(first_frame),
                   "last_frame": mapped(last_frame), "reference_images": [mapped(image) for image in reference_images]}
        result = await self._json("POST", self.connection.submit_path, json=render(self.connection.request, context))
        task_id = lookup(result, self.connection.task_id_path)
        if type(task_id) is int and task_id >= 0:
            task_id = str(task_id)
        if (not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", task_id)
                or ".." in task_id):
            raise ProviderError("视频渠道未返回有效任务编号；请核对渠道账单，不自动重发")
        return VideoGenerationHandle(task_id)

    async def poll_video(self, handle):
        result = await self._json("GET", self.connection.poll_path.replace("{task_id}", quote(handle.id, safe="")))
        state = lookup(result, self.connection.status_path)
        if type(state) not in (str, int):
            raise ProviderError("视频任务状态不是字符串或整数，保留原任务编号")
        if state in self.connection.failed:
            return VideoGenerationStatus("failed", error="渠道视频任务失败；请核对渠道记录后决定是否重新生成")
        if state in self.connection.succeeded:
            if self.connection.result_url_path:
                self._video_urls[handle.id] = safe_public_url(lookup(result, self.connection.result_url_path))
            return VideoGenerationStatus("succeeded", 100)
        if state in self.connection.processing:
            return VideoGenerationStatus("processing")
        raise ProviderError("视频渠道返回未配置的任务状态，保留原任务，不重新生成")

    async def download_video(self, handle):
        if self.connection.download_path:
            url = self.base_url + self.connection.download_path.replace("{task_id}", quote(handle.id, safe=""))
            options, headers = {}, self.headers()
        else:
            if handle.id not in self._video_urls:
                if (await self.poll_video(handle)).status != "succeeded":
                    raise ProviderError("原视频任务尚未完成")
            url, headers = safe_public_url(self._video_urls[handle.id]), {}
            options = {"transport": PublicTransport(), "trust_env": False}
        try:
            # Result URLs are untrusted; pin public DNS and never forward channel credentials.
            async with (httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False, **options)
                        if options else outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False)) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if self.connection.download_path:
                        self.check_release(response)
                    if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].strip().lower() not in {
                        "video/mp4", "video/webm", "application/octet-stream",
                    }:
                        raise ProviderError("视频下载接口未返回视频文件")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 500 * 1024 * 1024:
                            raise ProviderError("视频结果超过500MB")
            if not raw:
                raise ProviderError("视频结果为空")
            return bytes(raw)
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("视频结果下载失败，可继续下载原任务") from exc


def connection_version(connection):
    return "configured.v1:" + sha256(json.dumps(connection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
