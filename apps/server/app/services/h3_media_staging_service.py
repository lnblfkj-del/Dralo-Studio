"""Prepare project-owned images for a single MiniMax H3 V2 request.

Signed URLs are short-lived request inputs. Callers must not persist or log them.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from app.core.outbound_http import outbound_client
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import MediaFile
from app.providers.minimax_video_parameters import validate_h3_video_parameters
from app.services import storage_service

IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}


async def _verify_signed_url(url: str) -> None:
    """Probe a signed image without following redirects or downloading its body."""
    try:
        async with outbound_client(timeout=10, follow_redirects=False) as client:
            response = await client.head(url)
            if response.status_code in {200, 206}:
                return
            if response.status_code not in {403, 404, 405}:
                raise ConflictError("H3 参考素材签名地址不能从公网读取")
            async with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as probe:
                if probe.status_code == 403:
                    raise ConflictError("H3 参考素材签名过期、密钥无效或没有读取权限，请检查桶权限及签名有效期")
                if probe.status_code == 404:
                    raise ConflictError("H3 参考素材在桶中不存在，请检查存储桶及对象是否已被外部删除")
                if probe.status_code not in {200, 206}:
                    raise ConflictError("H3 参考素材签名地址不能从公网读取")
    except httpx.HTTPError:
        raise ConflictError("H3 参考素材签名地址不可达, 未提交视频任务") from None


def _public_https_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.fragment
    ):
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return True


def _inspect_image(media: MediaFile) -> dict[str, int]:
    root = settings.storage_path.resolve()
    source = (root / Path(media.file_path)).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ConflictError("H3 参考素材文件不存在或不在当前存储目录")
    size = source.stat().st_size
    if not 0 < size <= 30 * 1024 * 1024 or (media.size is not None and media.size != size):
        raise ConflictError("H3 参考图片大小与已保存媒体资料不符或超出 30 MB")
    try:
        with Image.open(source) as image:
            width, height, image_format = *image.size, image.format
            if image_format not in IMAGE_FORMATS:
                raise ConflictError("H3 参考图片格式不受官方 V2 接口支持")
            if not 256 <= width <= 5760 or not 256 <= height <= 5760:
                raise ConflictError("H3 参考图片宽高超出官方范围")
            if not 0.4 <= width / height <= 2.5:
                raise ConflictError("H3 参考图片画幅超出官方范围")
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ConflictError("H3 参考图片无法读取或校验") from exc
    if ((media.width is not None and media.width != width)
            or (media.height is not None and media.height != height)):
        raise ConflictError("H3 参考图片尺寸与已保存媒体资料不一致")
    with source.open("rb") as stream:
        digest = sha256(stream.read()).hexdigest()
    if media.hash and digest != media.hash:
        raise ConflictError("H3 参考图片文件已变化, 请重新确认素材版本")
    return {"width": width, "height": height, "size_bytes": size, "sha256": digest}


async def stage_h3_image_media(
    session: AsyncSession, *, owner_id: int, project_id: int,
    input_contract: dict, confirm_upload: bool = False, storage_reference: dict | None = None,
) -> dict[int, dict]:
    """Upload only after explicit confirmation; return URLs for immediate use."""
    if (input_contract.get("schema_version") != "video_input_contract.v2"
            or input_contract.get("protocol") != "minimax_video_v2"
            or input_contract.get("ready") is not True):
        raise ConflictError("H3 视频输入尚未通过预检")
    if input_contract.get("blockers") or input_contract.get("required_confirmations"):
        raise ConflictError("H3 视频输入仍有待解决的问题")
    refs = input_contract.get("effective_references")
    if not isinstance(refs, list):
        raise ConflictError("H3 视频输入缺少已冻结的素材清单")
    roles = {"first_frame": [], "last_frame": [], "reference_image": []}
    for item in refs:
        if not isinstance(item, dict) or item.get("role") not in roles:
            raise ConflictError("H3 素材清单包含未知用途")
        media_id = item.get("media_id")
        if type(media_id) is not int or media_id <= 0:
            raise ConflictError("H3 素材清单包含无效媒体 ID")
        roles[item["role"]].append(media_id)
    if any(len(set(roles[role])) > 1 for role in ("first_frame", "last_frame")):
        raise ConflictError("H3 首尾帧各只能选用一个媒体版本")
    media_ids = list(dict.fromkeys(item["media_id"] for item in refs))
    validated = validate_h3_video_parameters(
        input_contract.get("model_id"), input_contract.get("effective_parameters") or {},
        first=bool(roles["first_frame"]), last=bool(roles["last_frame"]),
        references=len(set(roles["reference_image"])),
    )
    if validated["input_mode"] != input_contract.get("input_mode"):
        raise ConflictError("H3 素材用途与冻结输入模式不一致")
    config = None
    if settings.runtime_execution_location == "cloud":
        from app.services import private_storage_service
        if storage_reference is None:
            raise private_storage_service.missing("H3 任务未绑定本人对象存储版本，未提交模型请求")
        config = await private_storage_service.h3_config(session, owner_id, ref=storage_reference, images=bool(media_ids))
    if not media_ids:
        return {}
    if not confirm_upload:
        raise ConflictError("上传参考素材到对象存储需要明确确认")
    config = config if config is not None else storage_service.read_config()
    if not config.get("enabled") or not config.get("credentials"):
        raise ConflictError("H3 参考素材需要先启用带短时签名 URL 的对象存储")
    if not _public_https_url(config.get("endpoint") or ""):
        raise ConflictError("H3 对象存储 Endpoint 必须是公网 HTTPS 地址")
    if type(config.get("signed_url_seconds")) is not int or config["signed_url_seconds"] < 3600:
        raise ConflictError("H3 素材短时链接有效期至少需要 1 小时")

    media_by_id, metadata = await inspect_h3_references(session, owner_id=owner_id,
                                                       project_id=project_id, media_ids=media_ids)
    result = {}
    for media_id in media_ids:
        published = await asyncio.to_thread(
            storage_service.mirror_media, media_by_id[media_id], make_url=True,
            **({"value": config} if settings.runtime_execution_location == "cloud" else {}),
        )
        url = published.get("url") if isinstance(published, dict) else None
        if not isinstance(published, dict) or published.get("uploaded") is not True:
            raise ConflictError("H3 对象存储未确认素材上传完成")
        if not isinstance(url, str) or not _public_https_url(url):
            raise ConflictError("H3 对象存储没有返回可供渠道访问的 HTTPS 素材地址")
        try:
            after_upload = await asyncio.to_thread(_inspect_image, media_by_id[media_id])
        except ConflictError as exc:
            raise ConflictError("H3 参考图片在上传期间发生变化") from exc
        if after_upload != metadata[media_id]:
            raise ConflictError("H3 参考图片在上传期间发生变化")
        await _verify_signed_url(url)
        result[media_id] = {**metadata[media_id], "url": url}
    if settings.runtime_execution_location == "cloud":
        # A clear during the upload must block the subsequent paid submission.
        await private_storage_service.resolve_reference(session, owner_id, storage_reference)
    return result


async def inspect_h3_references(session, *, owner_id, project_id, media_ids):
    rows = (await session.scalars(select(MediaFile).where(MediaFile.id.in_(media_ids)))).all()
    media_by_id = {media.id: media for media in rows}
    from app.services.team_access import same_team
    for media_id in media_ids:
        media = media_by_id.get(media_id)
        if (media is None or media.kind != "image"
                or (settings.runtime_execution_location == "cloud" and media.owner_id != owner_id)
                or not await same_team(session, media.owner_id, owner_id)
                or media.project_id not in {None, project_id}):
            raise ConflictError("H3 参考图片不属于当前项目或无权访问")
    metadata = {media_id: await asyncio.to_thread(_inspect_image, media_by_id[media_id]) for media_id in media_ids}
    return media_by_id, metadata
