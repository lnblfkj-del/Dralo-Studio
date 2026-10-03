"""ToAPIs video contract (official documentation verified 2026-09-05).

Keep the existing OpenAI-compatible setting; route only exact official endpoints.
Local media stays authenticated in our app, and is uploaded to ToAPIs only when
the user submits a generation. Never send local URLs or base64 to generation.
"""
# Chinese punctuation is intentional in user-visible messages.
# ruff: noqa: RUF001

import base64
import binascii
import re
from urllib.parse import urlsplit

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ConflictError, ProviderError, TimeoutError_
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.video import VideoGenerationHandle

VEO_LITE = "Veo3.1-lite-official"
VIDEO_MODELS = {
    "grok-video-1.0", "grok-video-1.5", "kling-v2-6", "viduq3",
    "viduq3-pro", "viduq3-turbo", VEO_LITE, "wan2.6", "wan2.6-flash",
}
VIDEO_MODELS.update({"seedance-2-5", "veo3.1-fast"})
VIDEO_MODELS.add("kling-v3-omni")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def model_defaults(model: str) -> dict:
    if model == "kling-v3-omni":
        from app.providers.toapis_omni import defaults
        return defaults()
    if model == "kling-v2-6":
        return {
            "duration": 5,
            "durations": [5, 10],
            "mode": "std",
            "resolution": "720p",
            "resolutions": ["720p", "1080p"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "supports_first_frame": True,
            "supports_last_frame": True,
            # ToAPIs does not publish a numeric limit. Keep the local editor bounded.
            "max_reference_images": 10,
            "supported_video_input_modes": [
                "text", "first_frame", "single_image", "multi_reference",
                "first_last_frame",
            ],
        }
    if model == "seedance-2-5":
        return {"duration":5,"durations":list(range(4,31)),"resolution":"720p","resolutions":["480p","720p","1080p"],
                "aspect_ratios":["16:9","9:16","1:1","4:3","3:4","21:9","adaptive"],
                "supports_first_frame":True,"supports_last_frame":False,"max_reference_images":4}
    if model == "veo3.1-fast":
        return {"duration":8,"durations":[8],"resolution":"720p","resolutions":["720p","1080p","4k"],
                "aspect_ratios":["16:9","9:16"],"supports_first_frame":True,"supports_last_frame":True,"max_reference_images":3}
    if model in {"wan2.6", "wan2.6-flash"}:
        return {"duration": 5, "durations": [5, 10, 15], "resolution": "720p",
                "resolutions": ["720p", "1080p"], "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "supports_first_frame": True, "supports_last_frame": False, "max_reference_images": 1}
    if model == VEO_LITE:
        # Limit Lite to its documented 720p tier; other Veo tiers are not aliases.
        return {"duration": 8, "durations": [4, 6, 8], "resolution": "720p",
                "resolutions": ["720p"], "aspect_ratios": ["16:9", "9:16"],
                "supports_first_frame": True, "supports_last_frame": False}
    if model == "grok-video-1.5":
        return {
            "duration": 8,
            "durations": list(range(1, 16)),
            "resolutions": ["480p", "720p"],
            "resolution": "720p",
            "aspect_ratios": ["16:9", "9:16", "1:1", "3:2", "2:3"],
            "supports_first_frame": True,
            "supports_last_frame": False,
            "max_reference_images": 7,
            "supported_video_input_modes": [
                "text", "first_frame", "single_image", "multi_reference",
            ],
        }
    grok = model.startswith("grok-")
    return {
        "duration": 8 if grok else 5,
        "durations": list(range(3 if model == "viduq3" else 1, 16 if grok else 17)),
        "resolutions": ["480p", "720p"] if grok else ["540p", "720p", "1080p"],
        "resolution": "720p",
        "aspect_ratios": ["16:9", "9:16", "1:1", "3:2", "2:3"] if grok else ["16:9", "9:16", "1:1"],
        "supports_first_frame": model != "viduq3",
        "supports_last_frame": model in {"viduq3-pro", "viduq3-turbo"},
    }


def is_toapis(base_url: str, protocol: str = "openai_compatible") -> bool:
    return protocol == "openai_compatible" and base_url.rstrip("/") in {
        "https://toapis.com/v1", "https://toapis.cn/v1",
    }


def video_parameters(model: str, parameters: dict, *, first: bool, last: bool, references: int) -> dict:
    """Validate before enqueue AND before upload. Never reinterpret two references as frames."""
    if model == "kling-v3-omni":
        from app.providers.toapis_omni import parameters as omni_parameters
        return omni_parameters(parameters, first=first, last=last, references=references)
    if model not in VIDEO_MODELS:
        if first or last or references:
            raise ConflictError("该 ToAPIs 模型的参考协议尚未适配；请选择已适配的视频模型")
        return parameters
    if model in {"seedance-2-5", "veo3.1-fast"}:
        return extended_video_parameters(model,parameters,first=first,last=last,references=references)
    if any(parameters.get(key) for key in ("image", "image_urls", "reference_images", "metadata", "subjects")):
        raise ConflictError("请通过画布素材引用选择图片，不能以扩展参数绕过素材权限或引用用途")
    if model == "kling-v2-6":
        limit, duration_default, minimum = 10, 5, 5
        resolutions = {"720p", "1080p"}
        if references > 10:
            raise ConflictError("Kling 2.6 当前编辑器最多接收 10 张普通参考图")
    elif model == VEO_LITE:
        if last or references:
            raise ConflictError("当前 Veo Lite 适配支持文生视频或单张首帧，请将图片用途设为首帧；尾帧/多参考模式尚未验收")
        if parameters.get("size") not in (None, parameters.get("aspect_ratio")):
            raise ConflictError("请通过 aspect_ratio 设置 Veo 画幅，不能同时传入不同的 size")
        limit, duration_default, minimum = 8, 8, 4
        resolutions = {"720p"}
    elif model in {"wan2.6", "wan2.6-flash"}:
        if last or int(first) + references > 1:
            raise ConflictError("Wan2.6 图生视频仅支持单张图片，不支持尾帧或多图输入")
        limit, duration_default, minimum = 15, 5, 5
        resolutions = {"720p", "1080p"}
        if model == "wan2.6-flash":
            if int(first) + references != 1:
                raise ConflictError("Wan2.6 Flash 需要一张图片，不支持纯文本；参考视频模式尚未接入")
            if any(parameters.get(key) is not None for key in ("seed", "prompt_extend", "shot_type", "generate_audio", "video_url", "reference_urls")):
                raise ConflictError("Wan2.6 Flash 当前仅接入单图、时长、分辨率、audio 和 watermark；不能绕过引用传入参考视频")
    elif model.startswith("grok-"):
        if last:
            raise ConflictError("Grok Video 不支持尾帧；首尾帧请选 viduq3-pro 或 viduq3-turbo")
        if model == "grok-video-1.5":
            if first and references:
                raise ConflictError("grok-video-1.5 首帧模式不能与普通参考图混用")
            if references > 7:
                raise ConflictError("grok-video-1.5 参考图模式最多支持 7 张图片")
        limit, duration_default, minimum = 15, 8, 1
        resolutions = {"480p", "720p"}
    else:
        limit, duration_default, minimum = 16, 5, 3 if model == "viduq3" else 1
        resolutions = {"540p", "720p", "1080p"}
        if model == "viduq3":
            if first or last or not references:
                raise ConflictError("viduq3 需要参考图，不支持指定首尾帧；首尾帧请选择 pro 或 turbo")
        elif (last and not first) or (references and (first or last or references > 1)):
            raise ConflictError("Vidu pro/turbo 请指定首帧及可选尾帧；多参考图请选择 viduq3")
    input_limit = 7 if model == "grok-video-1.5" else (12 if model == "kling-v2-6" else 4)
    if int(first) + int(last) + references > input_limit:
        raise ConflictError(f"当前模式最多支持 {input_limit} 个参考输入（含首尾帧）")
    result = dict(parameters)
    for field in ("duration", "resolution", "aspect_ratio"):
        if result.get(field) in (None, "default", "模型默认"):
            result.pop(field, None)
    result.setdefault("duration", duration_default)
    result.setdefault("resolution", "720p")
    duration = result["duration"]
    if type(duration) is float and duration.is_integer():
        duration = int(duration)
        result["duration"] = duration
    if model == "kling-v2-6" and (type(duration) is not int or duration not in {5, 10}):
        raise ConflictError("Kling 2.6 时长仅支持 5 或 10 秒")
    if model in {"wan2.6", "wan2.6-flash"} and (type(duration) is not int or duration not in {5, 10, 15}):
        raise ConflictError("Wan2.6 时长仅支持 5、10、15 秒")
    if type(duration) is not int or not minimum <= duration <= limit:
        raise ConflictError(f"{model} 的时长必须是 {minimum}–{limit} 秒整数")
    if model == VEO_LITE and duration not in {4, 6, 8}:
        raise ConflictError("Veo Lite 时长仅支持 4、6、8 秒")
    if not isinstance(result["resolution"], str) or result["resolution"] not in resolutions:
        raise ConflictError(f"{model} 不支持该分辨率")
    ratio = result.get("aspect_ratio")
    if model == "kling-v2-6":
        if ratio is not None and ratio not in {"16:9", "9:16", "1:1"}:
            raise ConflictError("Kling 2.6 画幅仅支持 16:9、9:16 或 1:1")
        mode = result.get("mode", "std")
        if mode not in {"std", "pro"}:
            raise ConflictError("Kling 2.6 mode 仅支持 std 或 pro")
        expected_resolution = "720p" if mode == "std" else "1080p"
        if result["resolution"] != expected_resolution:
            raise ConflictError(f"Kling 2.6 {mode} 模式必须使用 {expected_resolution}")
        result["mode"] = mode
        for field in ("audio", "watermark"):
            if field in result and type(result[field]) is not bool:
                raise ConflictError(f"{field} 必须为布尔值")
        if result.get("audio") and mode != "pro":
            raise ConflictError("Kling 2.6 自动音频仅支持 pro 模式")
    if model in {"wan2.6", "wan2.6-flash"}:
        if ratio is not None and (not isinstance(ratio, str) or ratio not in {"16:9", "9:16", "1:1", "4:3", "3:4"}):
            raise ConflictError("Wan2.6 不支持该画幅比例")
        for field in ("prompt_extend", "watermark"):
            if field in result and type(result[field]) is not bool:
                raise ConflictError(f"{field} 必须为布尔值")
        if result.get("shot_type") is not None and result["shot_type"] not in ("single", "multi"):
            raise ConflictError("Wan2.6 shot_type 仅支持 single 或 multi")
        if first or references:
            if result.get("shot_type") is not None:
                raise ConflictError("Wan2.6 图生视频不支持 shot_type，请移除该参数")
            result.pop("aspect_ratio", None)
    if model == VEO_LITE and ratio is not None and (not isinstance(ratio, str) or ratio not in {"16:9", "9:16"}):
        raise ConflictError("Veo Lite 画幅仅支持 16:9 或 9:16")
    if ratio is not None and (not isinstance(ratio, str) or (model.startswith("grok-") and ratio not in {"16:9", "9:16", "1:1", "3:2", "2:3"})):
        raise ConflictError("Grok Video 不支持该画幅比例")
    if "audio" in result and type(result["audio"]) is not bool:
        raise ConflictError("audio 必须为布尔值")
    if "seed" in result and type(result["seed"]) is not int:
        raise ConflictError("seed 必须为整数")
    return result


def extended_video_parameters(model, parameters, *, first, last, references):
    # Do not expose raw media URLs or upstream control fields through arbitrary JSON.
    forbidden=("metadata","image","image_urls","reference_images","image_with_roles","video_with_roles","audio_with_roles",
               "reference_urls","video_url","audio_url","callback_url","tools","return_last_frame","seed","audio","watermark","size","generation_type")
    if any(parameters.get(key) is not None for key in forbidden):
        raise ConflictError("该视频模式含未接入的扩展字段；素材须经节点权限解析，音视频参考与编辑尚未开放")
    if parameters.get("video_operation", "generate") != "generate" or parameters.get("output_format","mp4") != "mp4":
        raise ConflictError("当前仅开放生成 MP4，不支持编辑、延长或 MOV")
    if parameters.get("n",1) != 1:
        raise ConflictError("每个视频任务只生成一个结果，请使用批量任务")
    if (last and not first) or (references and (first or last)):
        raise ConflictError("首尾帧不能与普通参考图混用，尾帧必须有首帧")
    if model == "seedance-2-5" and last:
        raise ConflictError("Seedance 2.5 首尾帧要求自动时长协议，当前仅开放文生、单首帧及普通参考图")
    limit = 4 if model=="seedance-2-5" else 3
    if references>limit:
        raise ConflictError(f"当前此模式最多接收 {limit} 张普通参考图")
    defaults=model_defaults(model)
    result={key:parameters.get(key,defaults[key]) for key in ("duration","resolution")}
    result["aspect_ratio"]=parameters.get("aspect_ratio") or "16:9"
    for key,allowed in (("duration",defaults["durations"]),("resolution",defaults["resolutions"]),("aspect_ratio",defaults["aspect_ratios"])):
        if result[key] not in allowed or (key=="duration" and type(result[key]) is not int):
            raise ConflictError(f"{model} 的 {key} 不在已适配范围内")
    if parameters.get("generate_audio") is not None:
        if model != "seedance-2-5" or type(parameters["generate_audio"]) is not bool:
            raise ConflictError("当前 generate_audio 仅适用于 Seedance 2.5 且必须为布尔值")
        result["generate_audio"]=parameters["generate_audio"]
    return result


def decode_image(value: str) -> tuple[bytes, str]:
    try:
        header, content = value.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")
        if not header.startswith("data:") or not header.endswith(";base64") or mime not in IMAGE_TYPES:
            raise ValueError
        if len(content) > 14 * 1024 * 1024:
            raise ValueError
        data = base64.b64decode(content, validate=True)
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError
        return data, mime
    except (ValueError, binascii.Error) as exc:
        raise ConflictError("ToAPIs 参考图片需为 JPEG/PNG/WebP/GIF，单张不超过 10 MB") from exc


class ToAPIsProvider(OpenAICompatibleProvider):
    async def submit_video(self, *, model, prompt, negative_prompt, first_frame, last_frame, reference_images, parameters):
        business_id = parameters.get("_client_business_id")
        if business_id is not None and (
            not isinstance(business_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", business_id)
        ):
            raise ConflictError("ToAPIs 视频业务编号格式无效")
        public_parameters = {
            key: value for key, value in parameters.items()
            if key != "_client_business_id"
        }
        params = video_parameters(model, public_parameters, first=bool(first_frame), last=bool(last_frame), references=len(reference_images))
        if model == "kling-v3-omni":
            from app.providers.toapis_omni import prompt_with_image_references
            prompt = prompt_with_image_references(prompt, first=bool(first_frame), last=bool(last_frame), references=len(reference_images))
        if model not in VIDEO_MODELS:
            return await super().submit_video(model=model, prompt=prompt, negative_prompt=negative_prompt,
                first_frame=first_frame, last_frame=last_frame, reference_images=reference_images, parameters=params)
        if negative_prompt and model not in {"wan2.6", "kling-v2-6"}:
            raise ConflictError("此 ToAPIs 视频协议未声明负面提示词，请合并到生成要求中")
        # Validate every input before the first network operation; upload duplicates only once.
        images = {value: decode_image(value) for value in [first_frame, last_frame, *reference_images] if value}
        if model in {VEO_LITE,"veo3.1-fast","seedance-2-5"} and any(mime == "image/gif" for _, mime in images.values()):
            raise ConflictError("Veo Lite 首帧不支持 GIF，请使用 JPEG/PNG/WebP")
        allowed = {"duration", "resolution", "aspect_ratio"}
        if model == "kling-v2-6":
            allowed = {"duration", "aspect_ratio", "mode", "audio", "watermark"}
        if model == "kling-v3-omni":
            allowed = {"duration", "aspect_ratio", "mode", "audio"}
        if model.startswith("viduq3"):
            allowed |= {"audio", "seed"}
        if model == "wan2.6":
            allowed |= {"audio", "seed", "prompt_extend", "shot_type", "watermark"}
        if model == "wan2.6-flash":
            allowed |= {"audio", "watermark"}
        if model == "seedance-2-5":
            allowed |= {"generate_audio"}
        payload = {"model": model, "prompt": prompt, **{key: value for key, value in params.items() if key in allowed}}
        if business_id is not None:
            payload["client_business_id"] = business_id
        if model == "wan2.6" and negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if model == "kling-v2-6" and negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if model == VEO_LITE:
            payload["size"] = payload.pop("aspect_ratio", "16:9")
        if model == "veo3.1-fast":
            payload["metadata"]={"resolution":payload.pop("resolution")}
            if first_frame:
                payload["metadata"]["generation_type"]="frame"
            elif reference_images:
                payload["metadata"]["generation_type"]="reference"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url, follow_redirects=False) as client:
                urls = {}
                upload_diagnostic = {
                    "endpoint_host": urlsplit(self.base_url).hostname,
                    "endpoint_path": "/v1/uploads/images",
                }
                for value, (data, mime) in images.items():
                    try:
                        response = await client.post(
                            f"{self.base_url}/uploads/images", headers=headers,
                            files={"file": (f"reference.{IMAGE_TYPES[mime]}", data, mime)},
                            timeout=httpx.Timeout(min(self.timeout_seconds, 60), connect=min(self.timeout_seconds, 15)),
                        )
                        self._raise_for_provider_error(response)
                        body = response.json()
                        result = body.get("data") if isinstance(body, dict) else None
                        url = result.get("url") if isinstance(result, dict) else None
                        parsed = urlsplit(url) if isinstance(url, str) else None
                        if not isinstance(body, dict) or body.get("success") is not True or not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                            raise ProviderError("ToAPIs 图片上传失败或未返回有效 HTTPS 地址，未提交视频生成")
                    except httpx.TimeoutException as exc:
                        raise TimeoutError_("ToAPIs 参考图片上传超时，尚未提交视频生成", details=upload_diagnostic) from exc
                    except httpx.HTTPError as exc:
                        raise ProviderError("无法连接 ToAPIs 图片上传接口，尚未提交视频生成", details=upload_diagnostic) from exc
                    except ProviderError as exc:
                        exc.details = {**exc.details, **upload_diagnostic}
                        raise
                    except (TypeError, ValueError) as exc:
                        raise ProviderError("ToAPIs 图片上传响应格式无效，尚未提交视频生成", details=upload_diagnostic) from exc
                    urls[value] = url
                refs = [urls[value] for value in reference_images]
                if model == "kling-v3-omni":
                    image_list = [
                        {"image_url": urls[value], "type": role}
                        for value, role in ((first_frame, "first_frame"), (last_frame, "end_frame"))
                        if value
                    ] + [{"image_url": url} for url in refs]
                    metadata = {"image_list": image_list} if image_list else {}
                    if "watermark" in params:
                        metadata["watermark"] = params["watermark"]
                    if metadata:
                        payload["metadata"] = metadata
                elif model == "kling-v2-6":
                    roles = [(first_frame, "first_frame"), (last_frame, "last_frame")]
                    if first_frame or last_frame:
                        payload["image_with_roles"] = [
                            {"url": urls[value], "role": role}
                            for value, role in roles if value
                        ]
                    if refs:
                        payload["reference_images"] = refs
                elif model == "seedance-2-5":
                    roles=[(first_frame,"first_frame"),(last_frame,"last_frame"),*((value,"reference_image") for value in reference_images)]
                    if images:
                        payload["image_with_roles"]=[{"url":urls[value],"role":role} for value,role in roles if value]
                elif model == VEO_LITE:
                    if first_frame:
                        payload["image_urls"] = [urls[first_frame]]
                elif model == "grok-video-1.0":
                    if first_frame:
                        payload["image"] = urls[first_frame]
                    if refs:
                        payload["reference_images"] = refs
                elif model == "grok-video-1.5":
                    if first_frame:
                        payload["video_generation_mode"] = "first_frame_image_to_video"
                        payload["image"] = urls[first_frame]
                    elif refs:
                        payload["video_generation_mode"] = "reference_images_to_video"
                        payload["reference_images"] = refs
                    else:
                        payload["video_generation_mode"] = "text_to_video"
                else:
                    ordered = [urls[value] for value in (first_frame, last_frame) if value] or refs
                    if ordered:
                        payload["image_urls"] = ordered
                response = await client.post(f"{self.base_url}/videos/generations", headers=headers, json=payload)
                self._raise_for_provider_error(response)
                body = response.json()
                task_id = body.get("id") if isinstance(body, dict) else None
                if not isinstance(task_id, str) or not task_id:
                    raise ProviderError("ToAPIs 未返回视频任务 ID")
                return VideoGenerationHandle(id=task_id, status=str(body.get("status") or "queued"))
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 ToAPIs 上传或视频接口") from exc
        except (TypeError, ValueError) as exc:
            raise ProviderError("ToAPIs 返回的数据格式不兼容") from exc
