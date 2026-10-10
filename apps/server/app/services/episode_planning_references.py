"""Resolve authorized media identities, then bind them without model discretion."""

import json
from collections import Counter

from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError
from app.models import AssetVersion, ProjectAssetLink, ProjectMediaLink
from app.schemas.episode_references import SourceReferenceBinding
from app.schemas.episode_timing import ContentAnalysis
from app.services.media_core_service import get_owned_media


async def resolve_bindings(
    session, episode, sources, mode, bindings: tuple[SourceReferenceBinding, ...]
) -> list[dict]:
    if mode.input_mode not in {
        "text",
        "single_image",
        "multi_reference",
        "first_frame",
        "first_last_frame",
    }:
        raise ConflictError("未知参考素材输入模式")
    if mode.input_mode == "text" and bindings:
        raise ConflictError("纯文本模式不能隐式携带参考媒体，请选择对应模式")
    source_keys = {unit.key for unit in sources.units}
    limits = {limit.role: limit.maximum for limit in mode.reference_limits}
    resolved, seen, timed_sources = [], set(), set()
    per_source = {key: set() for key in source_keys}
    for binding in bindings:
        identity = (binding.media_id, binding.role)
        frame = binding.role in {"first_frame", "last_frame"}
        duplicate_key = (*identity, binding.source_keys) if frame else identity
        if duplicate_key in seen or not set(binding.source_keys) <= source_keys:
            raise ConflictError("参考媒体绑定重复或原文范围已变化")
        seen.add(duplicate_key)
        if frame != (mode.input_mode in {"first_frame", "first_last_frame"}):
            raise ConflictError("首尾帧模式不能与普通参考素材混用")
        if binding.role == "last_frame" and mode.input_mode != "first_last_frame":
            raise ConflictError("当前模式不支持尾帧")
        if limits.get(binding.role, 0) == 0:
            raise ConflictError("该模式不支持所选参考媒体类型")
        media = await get_owned_media(session, binding.media_id, episode.owner_id)
        if media.kind != ("image" if frame else binding.role) or media.purpose != "creative":
            raise ConflictError("参考素材类型与绑定角色不一致")
        if (
            not media.hash
            or len(media.hash) != 64
            or any(c not in "0123456789abcdef" for c in media.hash)
        ):
            raise ConflictError("参考素材缺少完整性指纹，请先完成媒体入库")
        version = link = None
        if binding.asset_version_id is not None:
            version = await session.get(AssetVersion, binding.asset_version_id)
            if version is None or version.media_file_id != media.id:
                raise NotFoundError("指定资产版本不存在或不属于该媒体")
            link = await session.scalar(
                select(ProjectAssetLink).where(
                    ProjectAssetLink.project_id == episode.project_id,
                    ProjectAssetLink.asset_id == version.asset_id,
                    ProjectAssetLink.production_archived.is_(False),
                )
            )
            if link is None:
                raise NotFoundError("指定资产未加入当前项目或已归档")
        elif media.project_id != episode.project_id:
            linked = await session.scalar(
                select(ProjectMediaLink.id).where(
                    ProjectMediaLink.project_id == episode.project_id,
                    ProjectMediaLink.media_file_id == media.id,
                )
            )
            if linked is None:
                raise NotFoundError("参考媒体未加入当前项目")
        timing = None
        if binding.use_audio_timing:
            from app.services.episode_audio_timing import resolve_speech_timing
            source_key = binding.source_keys[0]
            if source_key in timed_sources:
                raise ConflictError("同一条对白不能绑定多个实测配音")
            timed_sources.add(source_key)
            unit = next(unit for unit in sources.units if unit.key == source_key)
            timing = await resolve_speech_timing(session, media, unit)
        resolved.append(
            {
                "binding": binding.model_dump(mode="json"),
                "media": {
                    "id": media.id,
                    "hash": media.hash,
                    "kind": media.kind,
                    "file_path": media.file_path,
                    "size": media.size,
                    "width": media.width,
                    "height": media.height,
                    "duration": media.duration,
                    "mime_type": media.mime_type,
                },
                "asset_version": {
                    "id": version.id,
                    "asset_id": version.asset_id,
                    "version": version.version,
                }
                if version
                else None,
                "project_link_revision": link.production_revision if link else None,
                **({"speech_timing": timing} if timing else {}),
            }
        )
        for key in binding.source_keys:
            per_source[key].add(identity)
    for references in per_source.values():
        counts = Counter(role for _, role in references)
        if len(references) > mode.max_total_references or any(
            count > limits[role] for role, count in counts.items()
        ):
            raise ConflictError("同一段原文必需的参考素材已超过模型上限，不能静默丢弃")
    supplied = Counter(binding.role for binding in bindings)
    if any(supplied[limit.role] < limit.minimum for limit in mode.reference_limits):
        raise ConflictError("缺少该模式必需的参考媒体")
    if mode.input_mode in {"first_frame", "first_last_frame"} and not supplied["first_frame"]:
        raise ConflictError("首帧模式必须明确绑定起始画面")
    if mode.input_mode == "first_last_frame" and not supplied["last_frame"]:
        raise ConflictError("首尾帧模式必须明确绑定结束画面")
    validate_frame_coverage(sources, mode.input_mode, bindings)
    return resolved


def validate_frame_coverage(sources, input_mode, bindings):
    if input_mode not in {"first_frame", "first_last_frame"}:
        return
    positions = {unit.key: index for index, unit in enumerate(sources.units)}
    first = sorted(positions[b.source_keys[0]] for b in bindings if b.role == "first_frame")
    last = sorted(positions[b.source_keys[0]] for b in bindings if b.role == "last_frame")
    if not first or first[0] != 0 or len(first) != len(set(first)):
        raise ConflictError("首帧必须包含正文起始边界，且同一边界只能指定一张首帧")
    if input_mode == "first_last_frame" and (
        len(first) != len(last)
        or last[-1] != len(sources.units) - 1
        or any(start > end for start, end in zip(first, last, strict=True))
        or any(start != end + 1 for start, end in zip(first[1:], last[:-1], strict=True))
    ):
        raise ConflictError("首尾帧范围必须成对连续覆盖正文，不能留空、交叉或漏掉结尾")


def bind_analysis(
    analysis: ContentAnalysis, bindings: tuple[SourceReferenceBinding, ...]
) -> ContentAnalysis:
    if any(block.references for block in analysis.blocks):
        raise ValueError("Timing model cannot author media bindings")
    covered = {key for block in analysis.blocks for key in block.source_keys}
    if any(not set(binding.source_keys) <= covered for binding in bindings):
        raise ValueError("Timing analysis omitted a source required by a media binding")
    raw = analysis.model_dump(mode="json")
    for block in raw["blocks"]:
        frames = {"first_frame": [], "last_frame": []}
        for binding in bindings:
            if binding.role in frames and binding.source_keys[0] in block["source_keys"]:
                index = 0 if binding.role == "first_frame" else -1
                if binding.source_keys[0] != block["source_keys"][index]:
                    raise ValueError(
                        "首尾帧绑定落在镜头内部，请重新分析边界；不能移动参考帧或截短正文"
                    )
                frames[binding.role].append(binding)
        if any(len(values) > 1 for values in frames.values()):
            raise ValueError("同一镜头边界不能绑定多个首帧或尾帧")
        block["references"] = [
            {"media_id": binding.media_id, "role": binding.role}
            for binding in bindings
            if set(binding.source_keys).intersection(block["source_keys"])
        ]
    return ContentAnalysis.model_validate_json(json.dumps(raw))


def bound_analysis_prompt(
    sources, bindings: tuple[SourceReferenceBinding, ...], references=()
) -> str:
    from app.services.episode_content_timing import analysis_prompt

    boundaries = [
        {"role": binding.role, "source_key": binding.source_keys[0]}
        for binding in bindings
        if binding.role in {"first_frame", "last_frame"}
    ]
    prompt = analysis_prompt(sources)
    timings = [item["speech_timing"] for item in references if item.get("speech_timing")]
    if timings:
        prompt += (
            "\n以下完整对白已有配音实测。对应 event 的 minimum_ms/estimated_ms/maximum_ms "
            "必须均采用 duration_ms，保留自然停顿，不用目标时长压缩："
            + json.dumps(timings, ensure_ascii=False)
        )
    if boundaries:
        prompt += (
            "\n用户指定的画面边界：first_frame 对应来源必须位于一个 block 的开头；"
            "last_frame 对应来源必须位于一个 block 的末尾。不能移动来源、改写或截断对白。"
            "仍按自然表演估时，不为满足边界压缩时间；安全切点如实判断。\n"
            + json.dumps(boundaries, ensure_ascii=False)
        )
    return prompt
