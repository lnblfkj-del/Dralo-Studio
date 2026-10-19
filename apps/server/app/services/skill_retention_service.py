"""Keep only the current Skill catalog; jobs own their frozen execution content."""

from sqlalchemy import delete, select, update

from app.models import AgentSkill, AgentSkillVersion, BusinessExecutorSetting


async def retain_latest(session, skills: list[AgentSkill]) -> int:
    from app.services.agent_config_service import skill_snapshot

    await session.flush()
    ids = [skill.id for skill in skills]
    if not ids:
        return 0
    if session.get_bind().dialect.name == "sqlite":
        await session.execute(update(AgentSkill).where(AgentSkill.id.in_(ids)).values(
            version=AgentSkill.version,
        ).execution_options(synchronize_session=False))
    skills = list(await session.scalars(select(AgentSkill).where(AgentSkill.id.in_(ids))
        .order_by(AgentSkill.id).with_for_update().execution_options(populate_existing=True)))
    current = {skill.id: skill.version for skill in skills}
    removed = 0
    for skill in skills:
        snapshot = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == skill.id, AgentSkillVersion.version == skill.version,
        ))
        if snapshot is None:
            session.add(AgentSkillVersion(
                skill_id=skill.id, version=skill.version, workspace_id=skill.workspace_id,
                snapshot=skill_snapshot(skill),
            ))
        result = await session.execute(delete(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == skill.id, AgentSkillVersion.version != skill.version,
        ))
        removed += result.rowcount
    for setting in await session.scalars(select(BusinessExecutorSetting).order_by(BusinessExecutorSetting.id)
                                        .with_for_update().execution_options(populate_existing=True)):
        selections = [
            {**item, "version": current[item["skill_id"]]}
            if item["skill_id"] in current else dict(item)
            for item in setting.skill_versions or []
        ]
        if selections != setting.skill_versions:
            setting.skill_versions = selections
            setting.revision += 1
    await session.flush()
    return removed
