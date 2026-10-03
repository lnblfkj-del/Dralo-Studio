"""视频 Provider 的统一内部契约。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VideoGenerationHandle:
    """供应商任务句柄；仅在 Worker 内部使用。"""

    id: str
    status: str = "processing"


@dataclass(frozen=True)
class VideoGenerationStatus:
    status: str
    progress: int = 50
    error: str | None = None


class VideoProvider(Protocol):
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
    ) -> VideoGenerationHandle: ...

    async def poll_video(self, handle: VideoGenerationHandle) -> VideoGenerationStatus: ...

    async def download_video(self, handle: VideoGenerationHandle) -> bytes: ...
