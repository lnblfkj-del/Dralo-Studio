"""Guarded, transactional release of the reviewed builtin Skill instructions."""

# ruff: noqa: RUF001
import re

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import AgentSkill, AgentSkillVersion
from app.models.agent_config import BusinessExecutorSetting
from app.services.agent_config_service import skill_snapshot
from app.services.skill_runtime import DIRECTOR_CONTRACT

RELEASE_ID = "professional-20260909-r2"
CHARACTER_ECOSYSTEM_RELEASE_ID = "character-ecosystem-20260918-r3"
CHARACTER_ECOSYSTEM_SKILLS = {"outline.story_bible"}


async def _replace_instruction(session, row, instruction):
    # Instruction-only release: legacy cross-agent executor tool declarations
    # are preserved byte-for-byte, never expanded or reinterpreted here.
    row.instruction = instruction
    row.version += 1
    await session.flush()
    session.add(AgentSkillVersion(skill_id=row.id, version=row.version, snapshot=skill_snapshot(row)))
    await session.flush()


def instructions_from_review(document: str) -> dict[str, str]:
    common = document.split("## 2.", 1)[1].split("## 3.", 1)[0]
    common = common[common.index("你在短剧"):].split("后续实际配置时", 1)[0].strip()
    sections = re.findall(r"### \d+ [^\n]+ — ([\w.-]+)\n(.*?)(?=\n### |\n## 4\.)", document, re.S)
    result = {}
    for key, section in sections:
        body = section.split("**专属正文：**", 1)[1].strip()
        contract = (
            "运行入口适配：上述字段示例只在对应入口使用；实际调用入口的输出格式优先。"
            "若当前入口要求纯正文或不同JSON结构，按入口格式返回，不额外输出审查长表。"
            "只使用本次提供的上下文；未提供的资料不得声称已审查。"
        )
        if key.startswith("episode."):
            contract += DIRECTOR_CONTRACT
        if key.startswith("canvas.") and key.endswith("generate") and key != "canvas.text.generate":
            contract += "本技能用于创作指导与提案；媒体直接提交未自动运行本技能。不要将这些指令作为图像内容或TTS台词。"
        if key == "media.task-orchestrator.v1":
            contract += "本技能为程序编排职责说明，修改文字不改变实际调度、审批、费用或幂等规则，也不增加模型调用。"
        result[key] = f"{common}\n\n{body}\n\n{contract}"
    if len(result) != 19:
        raise ValueError("审核稿必须包含19项完整专属正文")
    return result


async def release(session, instructions: dict[str, str], *, apply=False):
    # Serialize writers before preview validation; callers own commit/rollback.
    await session.execute(update(AgentSkill).where(AgentSkill.key.in_(instructions)).values(version=AgentSkill.version))
    rows = list(await session.scalars(select(AgentSkill).where(AgentSkill.key.in_(instructions))))
    if {row.key for row in rows} != set(instructions):
        raise ConflictError("缺少审核稿对应技能，发布未执行")
    versions = {}
    changes = []
    for row in rows:
        current = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
        if current is None:
            raise ConflictError(f"{row.key} 缺少当前版本快照")
        marker = current.snapshot.get("professional_release", {})
        if row.instruction == instructions[row.key] and marker.get("id") == RELEASE_ID:
            versions[row.id] = row.version
            continue
        if not row.is_builtin or row.version != 1 or skill_snapshot(row) != current.snapshot:
            raise ConflictError(f"{row.key} 已被修改或不是原始内置版本，保护自定义内容，整批未发布")
        versions[row.id] = row.version + 1
        changes.append(row)
    bindings = list(await session.scalars(select(BusinessExecutorSetting)))
    changed_ids = {row.id for row in changes}
    binding_changes = []
    for binding in bindings:
        old = list(binding.skill_versions or [])
        new = []
        for item in old:
            target = versions.get(item["skill_id"])
            if target is not None and item["version"] != target and item["skill_id"] not in changed_ids:
                raise ConflictError(f"{binding.key} 在发布后改变了绑定，重复发布不覆盖用户选择")
            if target is not None and item["version"] not in {1, target}:
                raise ConflictError(f"{binding.key} 使用了其他固定版本，整批未发布")
            new.append({**item, "version": target} if target is not None else dict(item))
        if old != new:
            binding_changes.append((binding, old, new))
    report = {"release_id": RELEASE_ID, "skills": [row.key for row in changes],
              "bindings": [row.key for row, _, _ in binding_changes], "applied": apply}
    if not apply:
        return report
    binding_receipts = [{"key": row.key, "before": old, "after": new,
                         "after_revision": row.revision + 1} for row, old, new in binding_changes]
    for row in changes:
        await _replace_instruction(session, row, instructions[row.key])
        version = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
        version.snapshot = {**version.snapshot, "professional_release": {
            "id": RELEASE_ID, "previous_version": 1, "bindings": binding_receipts}}
    for row, _, new in binding_changes:
        row.skill_versions = new
        row.revision += 1
    await session.flush()
    return report


async def rollback_release(session, *, apply=False):
    published_ids = select(AgentSkillVersion.skill_id).where(
        AgentSkillVersion.version == 2,
        AgentSkillVersion.snapshot["professional_release"]["id"].as_string() == RELEASE_ID,
    )
    await session.execute(update(AgentSkill).where(AgentSkill.id.in_(published_ids)).values(version=AgentSkill.version))
    rows = list(await session.scalars(select(AgentSkill)))
    changes = []
    receipts = {}
    for row in rows:
        published = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == 2))
        marker = published.snapshot.get("professional_release", {}) if published else {}
        if marker.get("id") != RELEASE_ID:
            continue
        current = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
        if current and current.snapshot.get("professional_rollback") == RELEASE_ID:
            continue
        expected = {k: v for k, v in published.snapshot.items() if k != "professional_release"}
        if row.version != 2 or skill_snapshot(row) != expected:
            raise ConflictError(f"{row.key} 发布后已修改，拒绝覆盖回退")
        old = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == marker["previous_version"]))
        if old is None:
            raise ConflictError("旧版本快照缺失")
        changes.append((row, old.snapshot["instruction"]))
        receipts.update({item["key"]: item for item in marker["bindings"]})
    bindings = []
    for key, receipt in receipts.items():
        row = await session.scalar(select(BusinessExecutorSetting).where(BusinessExecutorSetting.key == key))
        if row is None or row.revision != receipt["after_revision"] or row.skill_versions != receipt["after"]:
            raise ConflictError(f"{key} 发布后已修改，拒绝覆盖回退")
        bindings.append((row, receipt["before"]))
    if apply:
        for row, instruction in changes:
            await _replace_instruction(session, row, instruction)
            current = await session.scalar(select(AgentSkillVersion).where(
                AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
            current.snapshot = {**current.snapshot, "professional_rollback": RELEASE_ID}
        for row, before in bindings:
            row.skill_versions = before
            row.revision += 1
        await session.flush()
    return {"release_id": RELEASE_ID, "restored": [row.key for row, _ in changes], "applied": apply}


async def release_character_ecosystem(session, instructions: dict[str, str], *, apply=False):
    selected = {key: instructions[key] for key in CHARACTER_ECOSYSTEM_SKILLS}
    await session.execute(update(AgentSkill).where(AgentSkill.key.in_(selected)).values(version=AgentSkill.version))
    rows = list(await session.scalars(select(AgentSkill).where(AgentSkill.key.in_(selected))))
    if {row.key for row in rows} != CHARACTER_ECOSYSTEM_SKILLS:
        raise ConflictError("缺少角色生态对应内置技能，发布未执行")
    changes = []
    versions = {}
    for row in rows:
        current = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
        if current is None or skill_snapshot(row) != {
            key: value for key, value in current.snapshot.items()
            if key not in {"professional_release", "character_ecosystem_release"}
        }:
            raise ConflictError(f"{row.key} 当前内容与版本快照不一致，拒绝覆盖")
        marker = current.snapshot.get("character_ecosystem_release", {})
        if row.instruction == selected[row.key] and marker.get("id") == CHARACTER_ECOSYSTEM_RELEASE_ID:
            versions[row.id] = row.version
            continue
        prior = current.snapshot.get("professional_release", {})
        if not row.is_builtin or row.version != 2 or prior.get("id") != RELEASE_ID:
            raise ConflictError(f"{row.key} 不是受支持的专业版V2，保护现有内容，发布未执行")
        versions[row.id] = row.version + 1
        changes.append((row, current))
    bindings = list(await session.scalars(select(BusinessExecutorSetting)))
    binding_changes = []
    for binding in bindings:
        old = list(binding.skill_versions or [])
        new = []
        for item in old:
            target = versions.get(item["skill_id"])
            if target is not None and item["version"] not in {target, target - 1}:
                raise ConflictError(f"{binding.key} 固定了其他Skill版本，整批未发布")
            new.append({**item, "version": target} if target is not None else dict(item))
        if old != new:
            binding_changes.append((binding, old, new))
    report = {"release_id": CHARACTER_ECOSYSTEM_RELEASE_ID, "skills": [row.key for row, _ in changes],
              "bindings": [row.key for row, _, _ in binding_changes], "applied": apply}
    if not apply:
        return report
    receipts = [{"key": row.key, "before": old, "after": new, "after_revision": row.revision + 1}
                for row, old, new in binding_changes]
    for row, _current in changes:
        previous_version = row.version
        await _replace_instruction(session, row, selected[row.key])
        published = await session.scalar(select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == row.id, AgentSkillVersion.version == row.version))
        published.snapshot = {**published.snapshot, "character_ecosystem_release": {
            "id": CHARACTER_ECOSYSTEM_RELEASE_ID, "previous_version": previous_version, "bindings": receipts}}
    for row, _, new in binding_changes:
        row.skill_versions = new
        row.revision += 1
    await session.flush()
    return report
