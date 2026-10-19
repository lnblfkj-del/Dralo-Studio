"""Freeze adopted voice samples; unsupported routes explicitly retain text only."""

import asyncio
import base64
from copy import deepcopy
from hashlib import sha256

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import AssetVersion, MediaFile, ProjectAssetLink
from app.providers.protocols import effective_protocol
from app.providers.video_reference_audio import (
    VideoAudioReference,
    supports_reference_audio,
    validate_reference_audio,
)
from app.services.team_access import same_team
from app.services.video_prompt_freeze_service import _digest


async def _content(media):
    try:
        return await _read_content(media)
    except OSError as exc:
        raise ConflictError("参考声线文件读取失败，已停止收费提交，请核对本地素材") from exc


async def _read_content(media):
    from app.services import canvas_processing_service as processing

    root = settings.storage_path.resolve()
    path = (root / media.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or not 0 < path.stat().st_size <= 15_000_000:
        raise ConflictError("参考声线文件不可用或超过 15 MB，请重新采用有效样本")
    suffix = path.suffix.lower()
    if suffix not in {".wav", ".mp3"}:
        raise ConflictError("此视频接法只接收 MP3/WAV 参考声线，请转换后重新采用")
    def read_bounded():
        with path.open("rb") as stream:
            return stream.read(15_000_001)
    raw = await asyncio.to_thread(read_bounded)
    if len(raw) != media.size or sha256(raw).hexdigest() != media.hash:
        raise ConflictError("参考声线文件与已采用版本不一致，已停止收费提交")
    metadata = await processing.probe(path)
    duration = metadata.get("duration")
    if (not metadata.get("has_audio") or metadata.get("width") or metadata.get("format") != suffix[1:]
            or duration is None or media.duration is None or abs(duration - media.duration) > 0.05):
        raise ConflictError("参考声线时长无法核验或发生变化，已停止收费提交")
    if not 2 <= duration <= 15:
        raise ConflictError("每段参考音频须为 2–15 秒，不会自动裁剪")
    ffmpeg, _ = processing.executables()
    await processing.command([ffmpeg, "-v", "error", "-xerror", "-err_detect", "explode",
                              "-protocol_whitelist", "file", "-format_whitelist", "mp3,wav",
                              "-i", str(path), "-map", "0:a:0", "-f", "null", "-"], timeout=30)
    value = "data:audio/" + suffix[1:] + ";base64," + base64.b64encode(raw).decode("ascii")
    return VideoAudioReference(media.id, value, duration)


async def prepare(session, project, refs, guidance, provider, model, input_contract):
    """Only samples explicitly adopted (or pinned) for an identified speaker qualify."""
    from app.services.asset_binding_service import resolve_asset_binding
    from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint

    guidance = deepcopy(guidance)
    protocol = effective_protocol(provider, model)
    enabled = supports_reference_audio(protocol, model.model_id)
    params = input_contract["effective_parameters"]
    policy = params.get("audio_policy") or {}
    reason = None
    if not enabled:
        reason = "当前渠道未适配参考音频，仅使用文字声线"
    elif params.get("generate_audio") is False or (policy.get("capability") or {}).get("output") == "unsupported":
        reason = "视频声音已关闭，参考音频不提交，需后期配音"
    elif input_contract.get("first_frame_media_id") or input_contract.get("last_frame_media_id") or not input_contract.get("reference_media_ids"):
        reason = "此接法的参考音频须搭配普通参考图，当前仅使用文字声线"
    references, statuses, audios = [], [], []
    for entry in guidance.get("entries", []):
        status = {"speaker_asset_id": entry["speaker_asset_id"], "speaker_name": entry["speaker_name"],
                  "delivery": "text_only", "reason": reason or "尚未采用参考声线，使用文字描述"}
        statuses.append(status)
        asset_id = entry.get("voice_asset_id")
        if entry.get("source") == "group_speech":
            status["reason"] = "多人合说保留各成员声线，不套用单人样本"
        if reason or asset_id is None or entry.get("source") == "group_speech":
            continue
        bindings = [item for item in refs.get("asset_bindings", []) if item.get("asset_id") == asset_id
                    and item.get("media_kind") == "audio"]
        if len(bindings) > 1:
            raise ConflictError("同一声线绑定了多个样本，请明确选择一个版本")
        if bindings:
            binding = bindings[0]
        else:
            link = await session.scalar(select(ProjectAssetLink).where(
                ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == asset_id))
            if not link or link.production_archived:
                raise ConflictError("参考声线资产已移除或归档")
            data = link.production_data or {}
            if "adoptions" in data and not data["adoptions"]:
                continue
            if "adoptions" not in data and not await session.scalar(select(AssetVersion.id).where(
                AssetVersion.asset_id == asset_id, AssetVersion.is_final.is_(True), AssetVersion.review_status != "archived").limit(1)):
                continue
            binding = await resolve_asset_binding(session, project, asset_id=asset_id, role="voice_reference")
        media = await session.get(MediaFile, binding["media_file_id"])
        if media is None or media.kind != "audio" or not await same_team(session, project.owner_id, media.owner_id):
            raise ConflictError("参考声线不存在或无权访问")
        if media.purpose != "voice_reference":
            status["reason"] = "采用音频未标记为参考声线，不将正式配音或配乐提交给视频模型"
            continue
        audio = await _content(media)
        index = next((i for i, item in enumerate(references, 1) if item["media_file_id"] == media.id), None)
        if index is None:
            audios.append(audio)
            index = len(references) + 1
            references.append({"media_file_id": media.id, "asset_id": asset_id,
                               "asset_version_id": binding["asset_version_id"], "hash": media.hash,
                               "size": media.size, "duration": media.duration, "mime_type": media.mime_type,
                               "index": index})
        status.update(delivery="audio_reference_planned", reason="拟随视频请求提交，尚未发送", audio_index=index,
                      media_file_id=media.id)
    validate_reference_audio(protocol, model.model_id, audios, first=bool(input_contract.get("first_frame_media_id")),
                             last=bool(input_contract.get("last_frame_media_id")), images=input_contract.get("reference_media_ids"), parameters=params)
    core = {"schema_version": "video_voice_reference.v1", "endpoint_fingerprint": video_prompt_endpoint_fingerprint(provider, model),
            "input_fingerprint": input_contract["fingerprint"], "references": references, "speakers": statuses}
    guidance["audio_reference"] = {**core, "fingerprint": _digest(core)}
    return guidance


def append_mapping(prompt, guidance):
    speakers = ((guidance or {}).get("audio_reference") or {}).get("speakers", [])
    lines = [f"- {item['speaker_name']}的声线参考@音频{item['audio_index']}；只参考音色，不朗读样本内容，台词严格使用本片段正文。"
             for item in speakers if item["delivery"] == "audio_reference_planned"]
    return prompt + "\n\n角色参考音频对应关系：\n" + "\n".join(lines) if lines else prompt


def frozen_contract(payload):
    return ((payload.get("sound_input") or {}).get("voice_guidance") or {}).get("audio_reference")


async def load(session, job, provider, model):
    """Validate the frozen record and actual bytes again before the paid POST."""
    contract = frozen_contract(job.payload)
    expected = (job.payload.get("video_prompt_freeze") or {}).get("voice_reference_fingerprint")
    if not contract and not expected:
        return ()  # Historical jobs must not gain inputs on retry.
    from app.services.video_prompt_compiler import video_prompt_endpoint_fingerprint

    if (not isinstance(contract, dict) or contract.get("schema_version") != "video_voice_reference.v1"
            or contract.get("fingerprint") != _digest({k: v for k, v in contract.items() if k != "fingerprint"})
            or expected != contract["fingerprint"]
            or contract.get("input_fingerprint") != job.payload["video_input_contract"]["fingerprint"]
            or contract.get("endpoint_fingerprint") != video_prompt_endpoint_fingerprint(provider, model)):
        raise ConflictError("参考声线与确认快照或视频渠道不一致，已停止收费提交")
    audios = []
    for item in contract["references"]:
        media = await session.get(MediaFile, item["media_file_id"])
        version = await session.get(AssetVersion, item["asset_version_id"])
        link = await session.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == job.project_id, ProjectAssetLink.asset_id == item["asset_id"]))
        if (not media or media.kind != "audio" or media.purpose != "voice_reference"
                or not version or version.asset_id != item["asset_id"] or version.media_file_id != media.id
                or version.review_status == "archived" or not link or link.production_archived
                or not await same_team(session, job.owner_id, media.owner_id)
                or any(getattr(media, key) != item[key] for key in ("hash", "size", "duration", "mime_type"))):
            raise ConflictError("已确认的参考声线版本已失效，已停止收费提交")
        audios.append(await _content(media))
    validate_reference_audio(effective_protocol(provider, model), model.model_id, audios,
                             first=bool(job.payload.get("first_frame_media_id")), last=bool(job.payload.get("last_frame_media_id")),
                             images=job.payload.get("reference_media_ids"), parameters=job.payload.get("parameters", {}))
    return tuple(audios)
