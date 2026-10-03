"""Compile stable character voice guidance for native segment video audio."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import Asset, Project, ProjectAssetLink, VideoSegment
from app.services.asset_production_service import read_production


def _text(value: Any) -> str:
    return str(value or "").strip()


def _names(asset: Asset, production: dict[str, Any]) -> set[str]:
    profile = production.get("profile") or {}
    values = [asset.name, asset.slug]
    for source in ((asset.attributes or {}).get("aliases"), profile.get("aliases")):
        if isinstance(source, list):
            values.extend(source)
    return {_text(value).removeprefix("@").casefold() for value in values if _text(value)}


def _voice_description(asset: Asset, production: dict[str, Any]) -> str:
    profile = production.get("profile") or {}
    parts = [_text(profile.get("voice"))]
    for key, label in (("pitch", "音高"), ("texture", "质感"), ("pace", "语速"), ("accent", "口音")):
        value = _text(profile.get(key))
        if value:
            parts.append(f"{label}：{value}")
    return "；".join(dict.fromkeys(part for part in parts if part))


async def compile_segment_voice_guidance(
    session: AsyncSession,
    project: Project,
    segment: VideoSegment,
    refs: dict[str, Any],
) -> dict[str, Any]:
    """Resolve dialogue speakers and freeze the prompt-level voice source.

    A bound voice asset wins when it is linked to the speaker. If it has no
    textual profile, the character's stable voice description is used. Native
    video providers currently receive this text guidance; audio bindings remain
    post-production evidence until a provider contract supports audio input.
    """
    structured = (segment.parameters or {}).get("structured_script")
    dialogue = structured.get("dialogue") if isinstance(structured, dict) else None
    lines = [item for item in dialogue or [] if isinstance(item, dict) and _text(item.get("text"))]
    if not lines:
        return {"schema_version": "segment_voice_guidance.v1", "entries": [], "prompt_text": ""}

    for line in lines:
        if not _text(line.get("speaker")) or line.get("speaker_source") not in {"source", "manual"}:
            raise ConflictError("来源台词没有明确说话人，必须人工确认后才能生成视频")

    bound_ids = {
        int(item["asset_id"])
        for item in refs.get("asset_bindings", [])
        if isinstance(item, dict) and type(item.get("asset_id")) is int
    }
    rows = list((await session.execute(
        select(Asset, ProjectAssetLink)
        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
        .where(
            ProjectAssetLink.project_id == project.id,
            or_(
                Asset.asset_type == "character",
                and_(Asset.asset_type == "voice", Asset.id.in_(bound_ids or {-1})),
            ),
        )
    )).all())
    productions: dict[int, dict[str, Any]] = {}
    for asset, _link in rows:
        productions[asset.id] = await read_production(
            session, project, asset.id, segment_id=segment.id
        )

    characters = [(asset, productions[asset.id]) for asset, _link in rows if asset.asset_type == "character"]
    speakers: dict[int, dict[str, Any]] = {}
    for line in lines:
        raw_name = _text(line.get("speaker"))
        key = raw_name.removeprefix("@").casefold()
        matches = [(asset, production) for asset, production in characters if key in _names(asset, production)]
        if len(matches) != 1:
            reason = "无法唯一匹配角色资产" if matches else "没有匹配的角色资产"
            raise ConflictError(f"说话人“{raw_name}”{reason}，请在片段正文中重新确认")
        asset, production = matches[0]
        entry = speakers.setdefault(asset.id, {
            "speaker_asset_id": asset.id,
            "speaker_name": asset.name,
            "voice_asset_id": None,
            "source": "character_voice_description",
            "description": "",
            "lines": [],
        })
        entry["lines"].append({
            "shot_id": line.get("shot_id"),
            "text": _text(line.get("text")),
            "tone": _text(line.get("tone")) or "未指定",
        })
        entry["description"] = _voice_description(asset, production)

    voices = [(asset, productions[asset.id]) for asset, _link in rows if asset.asset_type == "voice" and asset.id in bound_ids]
    for asset, production in voices:
        profile = production.get("profile") or {}
        linked_id = profile.get("character_asset_id")
        target = speakers.get(linked_id) if type(linked_id) is int else None
        if target is None and len(speakers) == 1 and len(voices) == 1:
            target = next(iter(speakers.values()))
        if target is None:
            continue
        target["voice_asset_id"] = asset.id
        voice_description = _voice_description(asset, production) or _text(production.get("prompt_anchor")) or _text(asset.description)
        if voice_description:
            target["description"] = voice_description
            target["source"] = "voice_asset_description"

    for entry in speakers.values():
        if not entry["description"]:
            raise ConflictError(
                f"角色“{entry['speaker_name']}”没有可用声音资产或声线描述，请先补充角色声线"
            )

    entries = list(speakers.values())
    prompt_lines = ["角色声音一致性（本片段必须沿用以下固定设定）："]
    for entry in entries:
        dialogue_text = "；".join(
            f"{item['tone']}地说：“{item['text']}”" for item in entry["lines"]
        )
        prompt_lines.append(f"- {entry['speaker_name']}：{entry['description']}。{dialogue_text}")
    return {
        "schema_version": "segment_voice_guidance.v1",
        "entries": entries,
        "prompt_text": "\n".join(prompt_lines),
    }


def append_voice_guidance(prompt: str, guidance: dict[str, Any]) -> str:
    voice_prompt = _text(guidance.get("prompt_text"))
    return f"{prompt.rstrip()}\n\n{voice_prompt}" if voice_prompt else prompt
