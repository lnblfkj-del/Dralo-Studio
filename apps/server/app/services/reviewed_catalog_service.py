"""Versioned product data shared by deployment upgrades and new workspaces."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.workspace_context import current_workspace
from app.models import AgentSkill, AgentSkillVersion, StylePreset
from app.models.agent_config import BusinessExecutorSetting, StyleCategory
from app.services.agent_config_service import SKILL_BEHAVIOR_FIELDS, skill_snapshot


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@lru_cache
def catalog():
    return json.loads(Path(__file__).with_name("reviewed_catalog.json").read_text(encoding="utf-8"))


def scope_id():
    context = current_workspace.get()
    return context.workspace_id if context else None


async def install_skills(session, *, upgrade=False):
    result = []
    changed = {}
    for entry in catalog()["skills"]:
        definition = entry["definition"]
        skill = await session.scalar(select(AgentSkill).where(AgentSkill.key == definition["key"], AgentSkill.workspace_id == scope_id()).with_for_update())
        if skill is None:
            skill = AgentSkill(**definition, version=entry["initial_version"], is_builtin=True, enabled=True)
            session.add(skill)
            await session.flush()
            session.add(AgentSkillVersion(skill_id=skill.id, version=skill.version, snapshot=skill_snapshot(skill)))
        elif upgrade:
            current = {key: getattr(skill, key) for key in SKILL_BEHAVIOR_FIELDS}
            desired = {key: definition[key] for key in SKILL_BEHAVIOR_FIELDS}
            if current != desired:
                if not skill.is_builtin or fingerprint(current) not in entry["previous_hashes"]:
                    raise ConflictError(f"{skill.key} 已修改，拒绝覆盖；本次升级回滚")
                old_version = skill.version
                skill.version = max(skill.version + 1, entry["initial_version"])
                for key, value in definition.items():
                    setattr(skill, key, value)
                session.add(AgentSkillVersion(skill_id=skill.id, version=skill.version, snapshot=skill_snapshot(skill)))
                changed[skill.id] = (old_version, skill.version)
        result.append(skill)
    await session.flush()
    # Advance only bindings using the version actually upgraded; preserve explicit older choices.
    for setting in await session.scalars(select(BusinessExecutorSetting).where(BusinessExecutorSetting.workspace_id == scope_id()).with_for_update()):
        selections = []
        for selection in setting.skill_versions or []:
            versions = changed.get(selection["skill_id"])
            selections.append({**selection, "version": versions[1]} if versions and selection["version"] == versions[0] else dict(selection))
        if selections != setting.skill_versions:
            setting.skill_versions = selections
            setting.revision += 1
    await session.flush()
    return result


async def install_styles(session, *, upgrade=False):
    from app.services.builtin_style_media_service import references
    media = await references(session)
    categories = {row.name: row.id for row in await session.scalars(select(StyleCategory).where(StyleCategory.workspace_id == scope_id()))}
    for position, name in enumerate(catalog()["categories"]):
        if name not in categories:
            row = StyleCategory(name=name, position=position)
            session.add(row)
            await session.flush()
            categories[name] = row.id
    for entry in catalog()["styles"]:
        row = await session.scalar(select(StylePreset).where(StylePreset.name == entry["name"], StylePreset.workspace_id == scope_id()).with_for_update())
        fields = ("modalities", "prompt_suffix", "negative_prompt")
        desired = {key: entry[key] for key in fields}
        ids = [categories[name] for name in entry["categories"]]
        if row is None:
            session.add(StylePreset(name=entry["name"], **desired, category_ids=ids,
                                    category_id=ids[0] if ids else None,
                                    preview_media_id=media.get(entry["name"]), reference_media_id=media.get(entry["name"]),
                                    default_params=entry["default_params"], enabled=entry["enabled"]))
        elif upgrade:
            current = {key: getattr(row, key) for key in fields}
            if current != desired and fingerprint(current) not in entry["previous_hashes"]:
                raise ConflictError(f"风格 {row.name} 已修改，拒绝覆盖；本次升级回滚")
            for key, value in desired.items():
                setattr(row, key, value)
            row.category_ids = list(dict.fromkeys([*(row.category_ids or []), *ids]))
            row.category_id = row.category_id or (ids[0] if ids else None)
            if row.preview_media_id is None and row.reference_media_id is None:
                row.preview_media_id = row.reference_media_id = media.get(entry["name"])
    await session.flush()
