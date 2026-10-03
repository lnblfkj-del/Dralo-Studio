"""MiniMax H3 V2 task transport; deliberately not registered for production."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import quote, urlsplit

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.newapi import download_stream
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.video import VideoGenerationHandle, VideoGenerationStatus


class MiniMaxVideoV2Transport(OpenAICompatibleProvider):
    """Transport for an already validated V2 body; no generic video mapping."""

    async def discover_models(self):
        raise ConflictError("MiniMax H3 没有已接入的动态模型发现; 请从官方模板登记精确模型 ID")

    async def submit_video(self, **_kwargs):
        raise ConflictError("MiniMax H3 生产提交尚未开放; 不能回退到 OpenAI 兼容视频接口")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with outbound_client(
                timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False
            ) as client:
                response = await client.request(
                    method,
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    **kwargs,
                )
            self._raise_for_provider_error(response)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("non-object response")
            return result
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 MiniMax V2 视频接口") from exc
        except ValueError as exc:
            raise ProviderError("MiniMax V2 返回无效 JSON 对象") from exc

    async def create_video_task(self, body: dict) -> VideoGenerationHandle:
        """Submit a prepared V2 body once; ambiguous responses must not be replayed."""
        result = await self._request("POST", "/v2/video_generation", json=body)
        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ProviderError("MiniMax 未返回有效任务 ID; 请凭渠道任务和账单核对, 不自动重发")
        return VideoGenerationHandle(task_id, "queued")

    async def poll_video(self, handle: VideoGenerationHandle) -> VideoGenerationStatus:
        result = await self._request(
            "GET", f"/v2/query/video_generation/{quote(handle.id, safe='')}"
        )
        task = result.get("task")
        if not isinstance(task, dict) or task.get("id") != handle.id:
            raise ProviderError("MiniMax 查询任务 ID 不匹配, 保留原任务 ID")
        status = task.get("status")
        if status == "succeeded":
            content = task.get("content")
            url = content.get("url") if isinstance(content, dict) else None
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (
                not parsed or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
            ):
                raise ProviderError("MiniMax 未返回有效 HTTPS 视频下载地址")
            host = parsed.hostname
            if host == "localhost" or host.endswith(".localhost"):
                raise ProviderError("MiniMax 视频下载地址不能指向本机")
            try:
                if not ip_address(host).is_global:
                    raise ProviderError("MiniMax 视频下载地址不能指向内网")
            except ValueError:
                pass
            self._video_urls[handle.id] = url
            return VideoGenerationStatus("succeeded", 100)
        if status in {"failed", "cancelled"}:
            return VideoGenerationStatus(
                "failed", 0, "MiniMax 任务失败或取消; 请凭任务 ID 查询渠道详情, 不重新提交"
            )
        if status not in {"queued", "running"}:
            raise ProviderError("MiniMax 返回未知视频状态, 保留原任务 ID, 不重新提交")
        return VideoGenerationStatus("processing", 0 if status == "queued" else 50)

    async def download_video(self, handle: VideoGenerationHandle) -> bytes:
        if handle.id not in self._video_urls:
            status = await self.poll_video(handle)
            if status.status != "succeeded":
                raise ProviderError("MiniMax 视频尚未成功完成")
        return await download_stream(self, self._video_urls[handle.id])
