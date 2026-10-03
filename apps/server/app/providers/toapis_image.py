"""Documented GPT-Image-2 asynchronous reference-image contract, 2026-09-05.

No speculative edits/mask/upscaling support. URLs and credentials never come
from canvas parameters. The worker owns persistence of the external task ID.
"""

# ruff: noqa: RUF001
from urllib.parse import quote, urlsplit

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.toapis import IMAGE_TYPES, decode_image

MODEL = "gpt-image-2"
RATIOS = (
    "1:1",
    "3:2",
    "2:3",
    "4:3",
    "3:4",
    "5:4",
    "4:5",
    "16:9",
    "9:16",
    "2:1",
    "1:2",
    "21:9",
    "9:21",
)
RESOLUTIONS = ("1k", "2k", "4k")


def parameters(model, values):
    if model != MODEL:
        raise ConflictError("此高级图片工具只适配了 ToAPIs gpt-image-2 协议")
    size = values.get("aspect_ratio") or values.get("size") or "1:1"
    resolution = values.get("resolution") or "1k"
    if size not in RATIOS or resolution not in RESOLUTIONS:
        raise ConflictError("GPT-Image-2 比例或分辨率不在已适配范围内")
    if values.get("n", 1) != 1:
        raise ConflictError("当前图片任务只支持一次生成一张结果")
    return {"size": size, "resolution": resolution, "n": 1, "response_format": "url"}


def safe_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    if (
        not parsed
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ProviderError("图片渠道没有返回有效 HTTPS 素材地址")
    # ToAPIs uploads/results must use its public file service, not arbitrary
    # localhost/private URLs supplied by an upstream response. No redirects.
    if parsed.hostname not in {
        "files.toapis.com",
        "files.toapis.cn",
        "files.toapis.xyz",
    } or parsed.port not in {None, 443}:
        raise ProviderError("图片文件域名尚未验证，已停止上传引用或下载")
    return value


async def submit(adapter, *, model, prompt, reference_images, values, business_id):
    params = parameters(model, values)
    if not prompt.strip() or len(prompt) > 32000 or len(reference_images) > 6:
        raise ConflictError("图片提示词或参考素材数量超出范围")
    images = {value: decode_image(value) for value in reference_images}
    headers = {"Authorization": f"Bearer {adapter.api_key}"}
    try:
        async with outbound_client(
            timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False
        ) as client:
            urls = {}
            for value, (content, mime) in images.items():
                response = await client.post(
                    adapter.base_url + "/uploads/images",
                    headers=headers,
                    files={"file": (f"reference.{IMAGE_TYPES[mime]}", content, mime)},
                )
                adapter._raise_for_provider_error(response)
                body = response.json()
                if body.get("success") is not True:
                    raise ProviderError("参考图片上传失败，未提交生成")
                urls[value] = safe_url(body["data"]["url"])
            response = await client.post(
                adapter.base_url + "/images/generations",
                headers=headers,
                json={
                    **params,
                    "model": model,
                    "prompt": prompt,
                    "client_business_id": business_id,
                    "reference_images": [urls[v] for v in reference_images],
                },
            )
            adapter._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body.get("id"), str) or not body["id"]:
                raise ProviderError("图片渠道未返回任务编号，请查询本次业务编号，不要重新生成")
            return body["id"]
    except httpx.TimeoutException as exc:
        raise TimeoutError_() from exc
    except httpx.HTTPError as exc:
        raise ProviderError("图片请求连接失败，请核对原任务") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("图片渠道返回的数据格式不兼容") from exc


async def poll(adapter, task_id):
    try:
        async with outbound_client(
            timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False
        ) as client:
            response = await client.get(
                adapter.base_url + "/images/generations/" + quote(task_id, safe=""),
                headers={"Authorization": f"Bearer {adapter.api_key}"},
            )
            if response.status_code == 429:
                try:
                    delay = max(5, min(60, int(response.headers.get("Retry-After", 10))))
                except ValueError:
                    delay = 10
                return {"status": "queued", "progress": 0, "retry_after": delay, "rate_limited": True}
            adapter._raise_for_provider_error(response)
            body = response.json()
            if body.get("status") not in {
                "queued",
                "in_progress",
                "completed",
                "failed",
                "cancelled",
                "canceled",
            }:
                raise ProviderError("图片任务状态未知，保留原任务编号")
            if body["status"] == "completed":
                items = body["result"]["data"]
                if len(items) != 1:
                    raise ProviderError("图片结果数量与单图任务不一致")
                body["image_url"] = safe_url(items[0]["url"])
            return body
    except httpx.TimeoutException as exc:
        raise TimeoutError_() from exc
    except httpx.HTTPError as exc:
        raise ProviderError("图片状态查询失败，保留原任务编号") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("图片任务状态格式不兼容") from exc


async def download(adapter, url):
    try:
        # Intentionally no Bearer header for the file service.
        async with (
            outbound_client(
                timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False
            ) as client,
            client.stream("GET", safe_url(url)) as response,
        ):
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ProviderError("图片结果超过 20 MB")
                chunks.append(chunk)
            if not size:
                raise ProviderError("图片结果为空")
            return {"image_bytes": b"".join(chunks)}
    except httpx.TimeoutException as exc:
        raise TimeoutError_() from exc
    except httpx.HTTPError as exc:
        raise ProviderError("图片下载失败，保留任务可再次查询下载") from exc
