"""Compile stable character voice guidance for native segment video audio."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.models import Asset, Episode, Project, ProjectAssetLink, VideoSegment
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
    *, policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve dialogue speakers and freeze the prompt-level voice source.

    A bound voice asset wins when it is linked to the speaker. If it has no
    textual profile, the character's stable voice description is used. Input
    audio is resolved separately by the registered video-reference contract.
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
        for item in [*refs.get("asset_bindings", []), *refs.get("asset_references", [])]
        if isinstance(item, dict) and type(item.get("asset_id")) is int
    }
    rows = list((await session.execute(
        select(Asset, ProjectAssetLink)
        .join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id)
        .where(
            ProjectAssetLink.project_id == project.id,
            ProjectAssetLink.production_archived.is_(False),
            or_(
                Asset.asset_type == "character",
                and_(Asset.asset_type == "voice", Asset.id.in_(bound_ids or {-1})),
            ),
        )
    )).all())
    from app.services.asset_segment_projection import eligible, voice_character_ids
    from app.services.segment_asset_references import identity as asset_identity
    episode = await session.get(Episode, segment.episode_id)
    source_units = [{"text": line, "source_lines": [number]} for number, line in
                    enumerate((episode.script or "").splitlines(), 1)
                    if any(item.get("source_text") == line for item in lines)]
    projected = {asset.id: asset_identity(asset, link) for asset, link in rows}
    rows = [(asset, link) for asset, link in rows if (
        asset.asset_type == "voice" and projected[asset.id]["character_identity"].get("scope") != "source"
    ) or eligible(projected[asset.id], episode, source_units or None)]
    productions: dict[int, dict[str, Any]] = {}
    for asset, _link in rows:
        productions[asset.id] = await read_production(
            session, project, asset.id, segment_id=segment.id
        )
        if projected[asset.id].get("field_trust"):
            profile = dict(productions[asset.id].get("profile") or {})
            for field in projected[asset.id]["field_trust"].get("fields") or {}:
                profile.pop(field, None)
            profile.update({key: value for key, value in projected[asset.id]["profile"].items() if value is not None})
            for override in productions[asset.id].get("scope_overrides") or []:
                if f"{override.get('target_type')}:{override.get('target_id')}" in (productions[asset.id].get("effective_scope_keys") or []):
                    profile.update(override.get("profile") or {})
            productions[asset.id]["profile"] = profile

    characters = [(asset, productions[asset.id]) for asset, _link in rows if asset.asset_type == "character"]
    from app.services.screenplay_source_parser import classify, identity_index, parse_sources
    catalog = [{"asset_type": "character", "asset_id": asset.id, "asset_name": asset.name,
                "aliases": projected[asset.id]["aliases"] if projected[asset.id].get("character_identity")
                else list(_names(asset, production))} for asset, production in characters]
    identities, _ = identity_index(catalog)
    source_records = []
    if any(_text(line.get("speaker")) in {"两人", "二人", "两位"} for line in lines):
        episode = await session.get(Episode, segment.episode_id)
        try:
            source_records = parse_sources([{"line": i, "text": text} for i, text in
                                           enumerate((episode.script or "").splitlines(), 1) if text.strip()], catalog)
        except ValidationError as exc:
            raise ConflictError("合说来源需要核对：" + exc.message) from exc
    speakers: dict[int, dict[str, Any]] = {}
    events = []
    for line in lines:
        raw_name = _text(line.get("speaker"))
        scene_people = set()
        if raw_name in {"两人", "二人", "两位"}:
            matching = [r for r in source_records if r.get("text") == line.get("source_text")]
            scopes = {tuple(r.get("speaker_refs", [])) for r in matching}
            if len(scopes) != 1:
                raise ConflictError("合说来源无法唯一定位，请在片段中写明参与角色")
            scene_people = set(next(iter(scopes)))
        try:
            explicit = line.get("voice_source") or {}
            if line.get("speaker_source") == "source" and explicit.get("speaker_kind") in {"device", "narration", "anonymous"}:
                identity = {"speaker_kind": explicit["speaker_kind"], "speaker_refs": []}
            else:
                identity = classify(f"{raw_name.removeprefix('@')}：{line['text']}", identities, scene_people, 1)
        except ValidationError as exc:
            raise ConflictError(f"说话人“{raw_name}”无法唯一匹配角色资产，请在片段正文中重新确认") from exc
        if identity.get("speaker_kind") not in {"character", "group", "device", "narration", "anonymous"}:
            raise ConflictError(f"说话人“{raw_name}”没有匹配的角色资产，请在片段正文中重新确认")
        event = {
            "shot_id": line.get("shot_id"),
            "speaker": raw_name, "speaker_kind": identity["speaker_kind"],
            "speaker_refs": identity["speaker_refs"],
            "text": _text(line.get("text")),
            "tone": _text(line.get("tone")) or "未指定",
        }
        events.append(event)
        for asset, production in characters:
            if asset.id not in identity["speaker_refs"]:
                continue
            entry = speakers.setdefault(asset.id, {
                "speaker_asset_id": asset.id, "speaker_name": asset.name,
                "voice_asset_id": None, "source": "character_voice_description",
                "description": _voice_description(asset, production), "lines": [],
            })
            if not entry["description"]:
                related = voice_character_ids(projected[asset.id], list(projected.values()))
                descriptions = {str(projected[value]["profile"].get("voice") or "").strip()
                                for value in related if value in projected}
                descriptions.discard("")
                if len(descriptions) == 1:
                    entry["description"] = next(iter(descriptions))
            from app.services.asset_character_representation import character_profile, is_group
            group = character_profile(asset.attributes, production)
            if is_group(group):
                entry.update(source="group_speech", character_form=group["character_form"],
                             description="多人按原文共同发声，保留不同成员声线，不转为单人独白")
            if identity["speaker_kind"] == "character":
                entry["lines"].append(event)

    voices = [(asset, productions[asset.id]) for asset, _link in rows if asset.asset_type == "voice" and asset.id in bound_ids]
    assigned = set()
    for asset, production in voices:
        profile = production.get("profile") or {}
        linked_ids = {value for value in [profile.get("character_asset_id"), profile.get("linked_character_asset_id"),
                                         *(profile.get("linked_character_asset_ids") or [])] if type(value) is int}
        matching = [entry for ident, entry in speakers.items()
                    if linked_ids & voice_character_ids(projected[ident], list(projected.values()))]
        if len(matching) > 1:
            raise ConflictError("一个参考声线关联了多个说话角色，请明确角色与声线的对应关系")
        target = matching[0] if matching else None
        if (target is None and not linked_ids and len(speakers) == 1 and len(voices) == 1
                and not (asset.attributes or {}).get("character_identity")
                and not (asset.attributes or {}).get("field_trust")):
            target = next(iter(speakers.values()))
        if target is None:
            continue
        if target.get("source") == "group_speech":
            continue
        if target["speaker_asset_id"] in assigned:
            raise ConflictError("同一说话角色关联了多个声音资产，请只保留本片段使用的声线")
        assigned.add(target["speaker_asset_id"])
        target["voice_asset_id"] = asset.id
        voice_description = _voice_description(asset, production)
        if not projected[asset.id].get("field_trust"):
            voice_description = voice_description or _text(production.get("prompt_anchor")) or _text(asset.description)
        if voice_description:
            target["description"] = voice_description
            target["source"] = "voice_asset_description"

    for entry in speakers.values():
        if not entry["description"]:
            capability = (policy or {}).get("capability") or {}
            if (policy or {}).get("voice_choice") == "model_choice" or capability.get("output") == "unsupported":
                entry["source"] = "model_choice" if capability.get("output") != "unsupported" else "postproduction"
                entry["description"] = "声线由模型决定，保持本片段内一致" if entry["source"] == "model_choice" else "待后期配音"
                continue
            raise ConflictError(
                f"角色“{entry['speaker_name']}”没有声线描述，请补充角色声线，或在规划设置中明确选择由模型决定；无需先生成 TTS 文件"
            )

    entries = list(speakers.values())
    prompt_lines = ["角色声音一致性（本片段必须沿用以下固定设定）："]
    for entry in entries:
        prompt_lines.append(f"- {entry['speaker_name']}：{entry['description']}。")
    for event in events:
        kind = "同时合说（只发声一次）" if event["speaker_kind"] == "group" else "说"
        prompt_lines.append(f"- 分镜{event['shot_id']}：{event['speaker']}以{event['tone']}语气{kind}正文对应台词，不重复朗读。")
    return {
        "schema_version": "segment_voice_guidance.v1",
        "entries": entries,
        "events": events,
        "prompt_text": "\n".join(prompt_lines),
    }


def append_voice_guidance(prompt: str, guidance: dict[str, Any]) -> str:
    voice_prompt = _text(guidance.get("prompt_text"))
    return f"{prompt.rstrip()}\n\n{voice_prompt}" if voice_prompt else prompt
