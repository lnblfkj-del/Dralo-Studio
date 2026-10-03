"""Anthropic Messages text contract; does not emulate Claude Code or tool execution."""
# ruff: noqa: RUF001
from time import perf_counter

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.http_errors import raise_for_provider_http_error


class AnthropicMessagesProvider:
    def __init__(self, *, base_url, api_key, timeout_seconds, proxy_url=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url

    async def _request(self, method, path, **kwargs):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path,
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, **kwargs)
            raise_for_provider_http_error(response)
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 Anthropic Messages 渠道，请检查地址和网络") from exc
        except ValueError as exc:
            raise ProviderError("Anthropic 渠道返回的数据格式不兼容") from exc

    async def discover_models(self):
        started = perf_counter()
        models, cursor = set(), None
        for _ in range(100):
            data = await self._request("GET", "/models", params={"limit": 100, **({"after_id": cursor} if cursor else {})})
            items = data.get("data")
            if not isinstance(items, list):
                raise ProviderError("Anthropic 模型列表格式不兼容")
            models.update(item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str))
            if not data.get("has_more"):
                return sorted(models), round((perf_counter() - started) * 1000)
            next_cursor = data.get("last_id")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise ProviderError("Anthropic 模型列表分页游标无效")
            cursor = next_cursor
        raise ProviderError("模型列表超过分页上限，请手动添加模型")

    async def generate_text(self, *, model, prompt, parameters):
        allowed = {"max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "system", "thinking"}
        payload = {key: value for key, value in parameters.items() if key in allowed}
        payload.setdefault("max_tokens", 4096)
        if type(payload["max_tokens"]) is not int or payload["max_tokens"] <= 0:
            raise ConflictError("max_tokens 必须是正整数")
        images = parameters.get("style_reference_images", [])
        content = [{"type": "text", "text": prompt}]
        for image in images:
            header, data = image.split(",", 1)
            content.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";")[0], "data": data}})
        payload.update(model=model, messages=[{"role": "user", "content": content if images else prompt}], stream=False)
        data = await self._request("POST", "/messages", json=payload)
        try:
            text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
            if not text and data.get("stop_reason") != "max_tokens":
                raise ValueError
            usage = data.get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
            completion = usage.get("output_tokens", 0)
            normalized = {"prompt_tokens": prompt_tokens, "completion_tokens": completion, "total_tokens": prompt_tokens + completion} if "input_tokens" in usage and "output_tokens" in usage else {}
            for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                if key in usage:
                    normalized[key] = usage[key]
            return {"text": text, "usage": normalized, "finish_reason": data.get("stop_reason")}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderError("Anthropic 未返回可用文本") from exc
