"""Versioned product data shared by deployment upgrades and new workspaces."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select, update

from app.core.errors import ConflictError
from app.core.workspace_context import current_workspace
from app.models import AgentSkill, AgentSkillVersion, StylePreset
from app.models.agent_config import StyleCategory
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


async def install_skills(session, *, upgrade=False, keys: set[str] | None = None):
    entries = catalog()["skills"]
    if keys is not None:
        unknown = keys - {entry["definition"]["key"] for entry in entries}
        if unknown:
            raise ValueError(f"Unknown catalog skills: {', '.join(sorted(unknown))}")
        entries = [entry for entry in entries if entry["definition"]["key"] in keys]
        if not entries:
            return []
    result = []
    if session.get_bind().dialect.name == "sqlite":
        await session.flush()
        await session.execute(update(AgentSkill).where(AgentSkill.workspace_id == scope_id())
                              .values(version=AgentSkill.version).execution_options(synchronize_session=False))
    elif session.get_bind().dialect.name == "postgresql":
        # A workspace may have no catalog rows yet; row locks alone cannot serialize first install.
        lock_key = int.from_bytes(hashlib.sha256(f"skill-catalog:{scope_id()}".encode()).digest()[:8], "big", signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    for entry in entries:
        definition = entry["definition"]
        skill = await session.scalar(select(AgentSkill).where(AgentSkill.key == definition["key"], AgentSkill.workspace_id == scope_id()).with_for_update().execution_options(populate_existing=True))
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
                skill.version = max(skill.version + 1, entry["initial_version"])
                # A behavioral upgrade must not reset a user's display name.
                for key, value in desired.items():
                    setattr(skill, key, value)
                session.add(AgentSkillVersion(skill_id=skill.id, version=skill.version, snapshot=skill_snapshot(skill)))
        result.append(skill)
    await session.flush()
    from app.services.skill_retention_service import retain_latest
    await retain_latest(session, result)
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
        elif (row.preview_media_id is None and row.reference_media_id is None
              and all(getattr(row, key) == value for key, value in desired.items())
              and row.default_params == entry["default_params"]):
            # Relink unchanged product styles after explicit resource installation/reset.
            # Never replace a user's image selection or customized style behavior.
            row.preview_media_id = row.reference_media_id = media.get(entry["name"])
    await session.flush()
