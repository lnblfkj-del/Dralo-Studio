"""Seed reviewed code-owned templates, never copy another user's database rows."""

from app.services.reviewed_catalog_service import install_skills


async def initialize(session, settings):
    bindings = {key: [] for key in ("outline", "script", "canvas", "market")}
    for skill in await install_skills(session):
        if skill.mode in bindings:
            bindings[skill.mode].append(skill.id)
        if skill.key == "outline.rewrite":
            settings.outline_agent_skill_id = skill.id
        if skill.key == "script.from_brief":
            settings.script_agent_skill_id = skill.id
    settings.agent_skill_bindings = bindings
    await session.flush()
