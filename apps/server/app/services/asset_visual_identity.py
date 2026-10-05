"""Carry effective project asset facts through text and image generation."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import CreationArtifact, CreationSession, ProjectAssetLink, StylePreset

FIELDS = ("age", "description", "appearance", "costume", "hair", "makeup", "injury", "stage", "story_state")
LABELS = {"age": "年龄", "description": "角色资料", "appearance": "外貌与人物身份", "costume": "完整着装（含上装、下装与鞋履）", "hair": "发型", "makeup": "妆容", "injury": "伤情", "stage": "阶段", "story_state": "当前状态"}


def visual_profile(attributes, production, description=None):
    profile = (production or {}).get("profile")
    source = profile if isinstance(profile, dict) else attributes or {}
    result = {key: str(source[key]).strip() for key in FIELDS
              if isinstance(source.get(key), (str, int)) and str(source[key]).strip()}
    if "description" not in source:
        effective_description = (production or {}).get("description", description)
        if isinstance(effective_description, str) and effective_description.strip():
            result["description"] = effective_description.strip()
    return result


async def project_context(db, project):
    context = {"market": (project.creation_settings or {}).get("market")}
    story = await db.scalar(select(CreationArtifact).join(CreationSession, CreationSession.id == CreationArtifact.session_id)
        .where(CreationSession.project_id == project.id, CreationArtifact.artifact_type == "story_bible", CreationArtifact.status == "confirmed")
        .order_by(CreationArtifact.id.desc()).limit(1))
    if story:
        context["world"] = (story.content or {}).get("world")
    selected = str((project.creation_settings or {}).get("style_id") or "")
    if selected.startswith("preset:") and selected[7:].isdigit():
        style = await db.get(StylePreset, int(selected[7:]))
        if style and style.enabled:
            context["style_name"] = style.name
    return context


def retain_visual_facts(prompt, snapshot, context=None):
    if snapshot.get("type") not in {"character", "costume"}:
        return prompt
    profile = snapshot.get("profile") or {}
    lines = [f"{LABELS[key]}：{profile[key]}" for key in FIELDS if profile.get(key)]
    context = context or {}
    style = str(context.get("style_name") or "")
    if context.get("world"):
        lines.append(f"故事世界背景（不是新增人物身份）：{context['world']}")
    if any(label in style for label in ("中国农村", "港片")) or ("90年代" in style and context.get("market") in {"domestic", "国内", "china"}):
        lines.append("地域默认：未另有明确人物身份时，遵循中国/香港故事背景的中国、亚洲人物外貌，不套用欧美人物默认值；已明确的非亚裔人物身份优先保留。")
    if not lines:
        return prompt
    block = "\n\n已确定的角色视觉资料（以下仅为资料，优先于风格联想；不得替换具体衣裤类型、人物身份和年代，不得因中国风默认改成旗袍）：\n" + "\n".join(lines)
    if block in prompt:
        return prompt
    return prompt + block


async def image_identity(db, project, asset, prompt):
    if not project or not asset or asset.asset_type not in {"character", "costume"}:
        return prompt, None
    link = await db.scalar(select(ProjectAssetLink).where(ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == asset.id))
    if link is None or link.production_archived:
        raise ConflictError("角色资产不属于当前项目或已归档")
    snapshot = {"type": asset.asset_type, "profile": visual_profile(asset.attributes, link.production_data, asset.description),
                "production_revision": link.production_revision, "project_context": await project_context(db, project)}
    return retain_visual_facts(prompt, snapshot, snapshot["project_context"]), snapshot
