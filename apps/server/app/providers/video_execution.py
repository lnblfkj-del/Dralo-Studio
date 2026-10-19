"""Single video execution boundary. Persistence and paid-submit intent stay in the Worker."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Literal

from app.core.errors import ConflictError
from app.providers.video import VideoGenerationHandle, VideoProvider
from app.providers.video_integrations import VideoIntegration
from app.providers.video_reference_audio import VideoAudioReference, validate_reference_audio


@dataclass(frozen=True)
class VideoMediaReference:
    media_id: int
    role: Literal["first_frame", "last_frame", "reference_image"]
    content: str

    def __post_init__(self):
        if (type(self.media_id) is not int or self.media_id <= 0
                or self.role not in {"first_frame", "last_frame", "reference_image"}
                or not isinstance(self.content, str) or not self.content):
            raise ConflictError("视频接入素材缺少精确版本、用途或已加载的内容")


@dataclass(frozen=True)
class VideoGenerationRequest:
    model: str
    prompt: str
    negative_prompt: str | None
    media: tuple[VideoMediaReference, ...]
    parameters: dict
    reference_audios: tuple[VideoAudioReference, ...] = ()

    @classmethod
    def from_payload(cls, payload, *, model, prompt, parameters, media_by_id, reference_audios=()):
        bindings = [
            (payload.get("first_frame_media_id"), "first_frame"),
            (payload.get("last_frame_media_id"), "last_frame"),
            *((media_id, "reference_image") for media_id in payload.get("reference_media_ids", [])),
        ]
        media = tuple(VideoMediaReference(media_id, role, media_by_id.get(media_id))
                      for media_id, role in bindings if media_id is not None)
        return cls(model, prompt, payload.get("negative_prompt"), media, deepcopy(parameters), reference_audios)

    def adapter_fields(self):
        frames = {role: [item.content for item in self.media if item.role == role]
                  for role in ("first_frame", "last_frame")}
        if any(len(items) > 1 for items in frames.values()):
            raise ConflictError("视频首帧和尾帧各只能绑定一个精确媒体版本")
        return {**{role: items[0] if items else None for role, items in frames.items()},
                "reference_images": [item.content for item in self.media if item.role == "reference_image"]}


class VideoExecution:
    def __init__(self, adapter: VideoProvider, integration: VideoIntegration):
        self.adapter = adapter
        self.integration = integration

    def poll_interval(self, configured_seconds):
        return max(getattr(self.adapter, "poll_interval_seconds", 0),
                   self.integration.minimum_poll_seconds, configured_seconds)

    def prepare(self, request: VideoGenerationRequest):
        fields = request.adapter_fields()
        validate_reference_audio(self.integration.key, request.model, request.reference_audios,
                                 first=bool(fields["first_frame"]), last=bool(fields["last_frame"]),
                                 images=fields["reference_images"], parameters=request.parameters)
        if self.integration.validator == "toapis" and request.model == "kling-v3-omni":
            from app.providers.toapis_omni import prompt_with_image_references

            prompt = prompt_with_image_references(request.prompt, first=bool(fields["first_frame"]),
                                                  last=bool(fields["last_frame"]), references=len(fields["reference_images"]))
            return replace(request, prompt=prompt)
        return request

    async def submit(self, request: VideoGenerationRequest, *, business_id=None, prepared_body=None):
        # No retries or alternate paths here: a timeout may still mean a paid task exists.
        if self.integration.staged_request:
            if request.reference_audios:
                raise ConflictError("当前分阶段接法尚未适配参考音频，已停止提交")
            if not isinstance(prepared_body, dict) or prepared_body.get("model") != request.model:
                raise ConflictError("视频接入缺少已核验且与当前模型一致的请求正文")
            return await self.adapter.create_video_task(deepcopy(prepared_body))
        if prepared_body is not None:
            raise ConflictError("当前视频接法不接受预组装请求正文")
        request = self.prepare(request)
        fields = request.adapter_fields()
        if request.reference_audios:
            fields["reference_audios"] = request.reference_audios
        parameters = deepcopy(request.parameters)
        if self.integration.client_business_id:
            if not isinstance(business_id, str) or not business_id.strip():
                raise ConflictError("视频任务缺少已冻结的业务编号，已停止提交")
            parameters["_client_business_id"] = business_id
        return await self.adapter.submit_video(model=request.model, prompt=request.prompt,
                                               negative_prompt=request.negative_prompt, parameters=parameters, **fields)

    async def poll(self, handle: VideoGenerationHandle):
        return await self.adapter.poll_video(handle)

    async def download(self, handle: VideoGenerationHandle):
        return await self.adapter.download_video(handle)
