"""Validated local image bytes and bounded MEAICC presigned uploads."""
import base64
import binascii
import hashlib
import io
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.core.outbound_http import outbound_client

MAX_BYTES = 10 * 1024 * 1024
MEDIA_HOST = "minioapi.meaicc.com"


def decode_image(value):
    try:
        header, encoded = value.split(",", 1)
        mime = {"data:image/png;base64": "image/png", "data:image/jpeg;base64": "image/jpeg"}[header]
        if len(encoded) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != ("PNG" if mime == "image/png" else "JPEG"):
                raise ValueError()
            if min(image.size) < 300 or image.width * image.height > 40_000_000:
                raise ValueError()
            image.verify()
        return raw, mime
    except (ValueError, KeyError, TypeError, AttributeError, OSError, binascii.Error,
            UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ConflictError("MEAICC 参考图须为本地 PNG/JPEG，宽高至少 300 像素，单图不超过 10 MB、4000 万像素") from exc


def validate_upload_url(value):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        if (not parsed or parsed.scheme != "https" or parsed.hostname != MEDIA_HOST
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ValueError()
    except ValueError as exc:
        raise ProviderError("MEAICC 返回的素材地址不在已接入的 HTTPS 上传域名内；已停止上传") from exc
    return value


async def upload_image(adapter, raw, mime):
    extension = "png" if mime == "image/png" else "jpg"
    filename = hashlib.sha256(raw).hexdigest() + "." + extension
    try:
        async with outbound_client(timeout=adapter.timeout_seconds, proxy=adapter.proxy_url, follow_redirects=False) as client:
            response = await client.get(f"https://{MEDIA_HOST}/api/get-upload-url",
                params={"filename": filename}, headers={"Authorization": f"Bearer {adapter.api_key}"})
            adapter._raise_for_provider_error(response)
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError("MEAICC 素材上传签名响应无效")
            upload_url = validate_upload_url(body.get("uploadUrl"))
            view_url = validate_upload_url(body.get("viewUrl"))
            # The bearer key goes only to the signing endpoint, never to the PUT URL.
            response = await client.put(upload_url, content=raw, headers={"Content-Type": mime})
            adapter._raise_for_provider_error(response)
            return view_url
    except httpx.TimeoutException as exc:
        raise TimeoutError_() from exc
    except httpx.HTTPError as exc:
        raise ProviderError("MEAICC 素材上传连接失败；未提交视频生成") from exc
    except ValueError as exc:
        raise ProviderError("MEAICC 素材上传签名未返回有效 JSON") from exc
