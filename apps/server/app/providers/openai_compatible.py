"""OpenAI-compatible Provider Adapter。"""

import asyncio
import base64
import binascii
import json
from time import perf_counter
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ProviderError, TimeoutError_
from app.providers.http_errors import (
    is_explicit_stream_unsupported,
    raise_for_provider_http_error,
)
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
        proxy_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url
        self._video_urls: dict[str, str] = {}

    async def discover_models(self) -> tuple[list[str], int]:
        started = perf_counter()
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接模型渠道，请检查 Base URL 和网络") from exc

        self._raise_for_provider_error(response)

        try:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError
            models = sorted(
                item["id"]
                for item in payload.get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError("模型渠道返回的数据格式不兼容") from exc
        return models, round((perf_counter() - started) * 1000)

    async def generate_text(
        self, *, model: str, prompt: str, parameters: dict[str, object]
    ) -> dict[str, object]:
        parameters = dict(parameters)
        images = parameters.pop("style_reference_images", [])
        content = ([{"type": "text", "text": prompt}, *[
            {"type": "image_url", "image_url": {"url": image}} for image in images
        ]] if images else prompt)
        payload = {
            **parameters,
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
        }
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接模型渠道，请检查网络") from exc
        self._raise_for_provider_error(response)
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            finish_reason = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("模型渠道返回的数据格式不兼容") from exc
        return {"text": text, "usage": usage, "finish_reason": finish_reason}

    async def generate_text_stream(
        self,
        *,
        model: str,
        prompt: str,
        parameters: dict[str, object],
        on_chunk: Callable[[str], Awaitable[None]],
    ) -> dict[str, object]:
        """Consume OpenAI-compatible SSE while retaining the same final result contract.

        Some compatibility gateways explicitly reject ``stream_options`` or streaming.
        Only that bounded diagnostic permits a non-streaming fallback; endpoint and
        ambiguous failures are never replayed.
        """
        original_parameters = dict(parameters)
        parameters = dict(parameters)
        first_byte_timeout = int(
            parameters.pop("_text_first_byte_timeout_seconds", self.timeout_seconds)
        )
        stream_idle_timeout = int(
            parameters.pop("_text_stream_idle_timeout_seconds", self.timeout_seconds)
        )
        images = parameters.pop("style_reference_images", [])
        content = ([{"type": "text", "text": prompt}, *[
            {"type": "image_url", "image_url": {"url": image}} for image in images
        ]] if images else prompt)
        payload = {
            **parameters,
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        chunks: list[str] = []
        usage: dict[str, object] = {}
        finish_reason: str | None = None
        done_marker_seen = False
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code in {400, 422}:
                        await response.aread()
                        if is_explicit_stream_unsupported(response):
                            return await self.generate_text(
                                model=model, prompt=prompt, parameters=original_parameters
                            )
                    if response.is_error:
                        await response.aread()
                    self._raise_for_provider_error(response)
                    lines = response.aiter_lines().__aiter__()
                    received_progress = False
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + first_byte_timeout
                    while True:
                        try:
                            line = await asyncio.wait_for(
                                anext(lines), timeout=max(0.001, deadline - loop.time())
                            )
                        except StopAsyncIteration:
                            break
                        except TimeoutError as exc:
                            stage = "流式空闲" if received_progress else "首响应等待"
                            raise TimeoutError_(f"文本模型{stage}超时") from exc
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if body == "[DONE]":
                            done_marker_seen = True
                            break
                        if not body:
                            continue
                        try:
                            event = json.loads(body)
                        except ValueError as exc:
                            raise ProviderError("模型渠道返回的流式数据格式不兼容") from exc
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        choices = event.get("choices")
                        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                            candidate_reason = choices[0].get("finish_reason")
                            if isinstance(candidate_reason, str) and candidate_reason:
                                finish_reason = candidate_reason
                        try:
                            delta_data = event["choices"][0].get("delta", {})
                            delta = delta_data.get("content")
                            reasoning = delta_data.get("reasoning_content")
                        except (KeyError, IndexError, TypeError, AttributeError):
                            delta = None
                            reasoning = None
                        if isinstance(delta, list):
                            delta = "".join(
                                str(part.get("text") or "")
                                for part in delta
                                if isinstance(part, dict)
                            )
                        if isinstance(delta, str) and delta:
                            chunks.append(delta)
                            await on_chunk(delta)
                        if (isinstance(delta, str) and delta) or (
                            isinstance(reasoning, str) and reasoning
                        ):
                            received_progress = True
                            deadline = loop.time() + stream_idle_timeout
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接模型渠道，请检查网络") from exc
        text = "".join(chunks)
        if not text and finish_reason not in {"length", "max_tokens", "max_output_tokens"}:
            raise ProviderError("模型渠道未返回可用的流式文本")
        return {
            "text": text, "usage": usage, "streaming": True,
            "finish_reason": finish_reason,
            "stream_terminal_seen": done_marker_seen or finish_reason is not None,
            "stream_done_marker_seen": done_marker_seen,
        }

    async def web_search(
        self, *, model: str, query: str, max_results: int = 10
    ) -> dict[str, object]:
        """Run provider-native Responses Web Search and normalize its result."""
        last_response: httpx.Response | None = None
        started = perf_counter()
        selected_tool = "web_search"
        for tool_type in ("web_search", "web_search_preview"):
            selected_tool = tool_type
            payload = {
                "model": model,
                "input": query,
                "tools": [{"type": tool_type}],
                "tool_choice": "required",
                "include": ["web_search_call.action.sources"],
            }
            try:
                async with outbound_client(
                    timeout=self.timeout_seconds,
                    proxy=self.proxy_url,
                    follow_redirects=False,
                ) as client:
                    response = await client.post(
                        f"{self.base_url}/responses",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                raise TimeoutError_() from exc
            except httpx.HTTPError as exc:
                raise ProviderError("无法连接模型原生 Web Search") from exc
            last_response = response
            if response.status_code not in {400, 404, 405, 422}:
                break
        assert last_response is not None
        response = last_response
        self._raise_for_provider_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Responses API 返回的数据格式不兼容") from exc
        text = self._responses_text(data)
        sources = self._responses_sources(data, max_results=max_results)
        if not text or not sources:
            raise ProviderError("模型渠道未返回可验证的 Web Search 来源")
        return {
            "text": text,
            "sources": sources,
            "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            "tool_type": selected_tool,
            "latency_ms": round((perf_counter() - started) * 1000),
        }

    @staticmethod
    def _responses_text(data: dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"].strip()
        chunks: list[str] = []
        for item in data.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        return "\n".join(chunks).strip()

    @staticmethod
    def _responses_sources(data: dict[str, Any], *, max_results: int) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                url = value.get("url")
                if (
                    isinstance(url, str)
                    and url.startswith(("https://", "http://"))
                    and url not in seen
                ):
                    seen.add(url)
                    found.append(
                        {
                            "title": str(value.get("title") or urlparse(url).hostname or url)[:300],
                            "url": url[:1000],
                        }
                    )
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data.get("output", []))
        return found[:max_results]

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        negative_prompt: str | None,
        reference_images: list[str],
        parameters: dict[str, object],
    ) -> dict[str, object]:
        from app.providers.image_parameters import openai_image_parameters
        payload = {**openai_image_parameters(model, parameters), "model": model, "prompt": prompt, "n": 1}
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        files = []
        if reference_images:
            for index, reference in enumerate(reference_images):
                try:
                    header, encoded = reference.split(",", 1)
                    mime = header.removeprefix("data:").removesuffix(";base64")
                    if header != f"data:{mime};base64" or mime not in {"image/png", "image/jpeg", "image/webp"}:
                        raise ValueError
                    content = base64.b64decode(encoded, validate=True)
                    if not content or len(content) > 20 * 1024 * 1024:
                        raise ValueError
                    files.append(("image[]", (f"reference-{index}.{mime.split('/')[-1]}", content, mime)))
                except (ValueError, TypeError, binascii.Error) as exc:
                    raise ProviderError("参考图必须是有效的 PNG、JPEG 或 WebP 数据，单张不超过 20 MB") from exc
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                if files:
                    response = await client.post(
                        f"{self.base_url}/images/edits",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data={key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in payload.items() if value is not None},
                        files=files,
                    )
                else:
                    response = await client.post(
                        f"{self.base_url}/images/generations",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                self._raise_for_provider_error(response)
                data = response.json()["data"][0]
                if isinstance(data.get("b64_json"), str):
                    image_bytes = base64.b64decode(data["b64_json"], validate=True)
                elif isinstance(data.get("url"), str):
                    url = data["url"]
                    if not url.startswith("https://"):
                        raise ProviderError("图片渠道返回了不安全的下载地址")
                    image_response = await client.get(url)
                    image_response.raise_for_status()
                    image_bytes = image_response.content
                else:
                    raise ProviderError("图片渠道未返回图片数据")
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            raise ProviderError("图片渠道返回的数据格式不兼容") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法下载图片生成结果") from exc
        if not image_bytes or len(image_bytes) > 20 * 1024 * 1024:
            raise ProviderError("图片生成结果为空或超过 20 MB")
        return {"image_bytes": image_bytes, "revised_prompt": data.get("revised_prompt")}

    async def submit_video(
        self,
        *,
        model: str,
        prompt: str,
        negative_prompt: str | None,
        first_frame: str | None,
        last_frame: str | None,
        reference_images: list[str],
        parameters: dict[str, object],
    ) -> VideoGenerationHandle:
        if first_frame or last_frame or reference_images:
            raise ProviderError("当前视频渠道暂只支持文生视频；参考图需先接入渠道上传协议")
        allowed = {
            "duration", "aspect_ratio", "resolution", "size", "mode", "seed",
            "watermark", "audio_setting", "generate_audio",
        }
        payload: dict[str, object] = {
            "model": model,
            "prompt": prompt,
            **{key: value for key, value in parameters.items() if key in allowed},
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/videos/generations",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
            self._raise_for_provider_error(response)
            data = response.json()
            task_id = data.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise ProviderError("视频渠道未返回任务 ID")
            return VideoGenerationHandle(id=task_id, status=str(data.get("status") or "queued"))
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接视频生成渠道") from exc
        except (TypeError, ValueError) as exc:
            raise ProviderError("视频渠道返回的数据格式不兼容") from exc

    async def poll_video(self, handle: VideoGenerationHandle) -> VideoGenerationStatus:
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/videos/generations/{handle.id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            self._raise_for_provider_error(response)
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法查询视频生成进度") from exc
        except (TypeError, ValueError) as exc:
            raise ProviderError("视频任务状态格式不兼容") from exc

        raw_status = str(data.get("status") or "").lower()
        progress_value = data.get("progress", 50)
        progress = progress_value if isinstance(progress_value, int) else 50
        if raw_status in {"completed", "succeeded", "success"}:
            video_url = data.get("video_url")
            if not isinstance(video_url, str):
                result = data.get("result")
                items = result.get("data") if isinstance(result, dict) else None
                first = items[0] if isinstance(items, list) and items else None
                video_url = first.get("url") if isinstance(first, dict) else None
            if not isinstance(video_url, str) or not video_url.startswith("https://"):
                return VideoGenerationStatus(status="failed", progress=progress, error="视频渠道未返回安全的下载地址")
            self._video_urls[handle.id] = video_url
            return VideoGenerationStatus(status="succeeded", progress=100)
        if raw_status in {"failed", "cancelled", "canceled"}:
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            return VideoGenerationStatus(status="failed", progress=progress, error=str(message or "视频生成失败"))
        return VideoGenerationStatus(status="processing", progress=max(0, min(progress, 99)))

    async def download_video(self, handle: VideoGenerationHandle) -> bytes:
        url = self._video_urls.get(handle.id)
        if url is None:
            raise ProviderError("视频任务尚未返回下载地址")
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > 500 * 1024 * 1024:
                            raise ProviderError("视频生成结果超过 500 MB")
                        chunks.append(chunk)
            data = b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法下载视频生成结果") from exc
        if not data:
            raise ProviderError("视频生成结果为空")
        return data

    @staticmethod
    def _raise_for_provider_error(response: httpx.Response) -> None:
        raise_for_provider_http_error(response)
