"""Resolve a project's shared style for generation."""

from app.models import Project, StylePreset
from app.services.team_access import same_team


async def project_style_media(session, project_id: int | None, owner_id: int, modality: str) -> int | None:
    if not project_id:
        return None
    project = await session.get(Project, project_id)
    if not project or not await same_team(session, project.owner_id, owner_id):
        return None
    selected = str((project.creation_settings or {}).get("style_id", ""))
    if not selected.startswith("preset:") or not selected[7:].isdigit():
        return None
    style = await session.get(StylePreset, int(selected[7:]))
    if not style or not style.enabled or modality not in style.modalities:
        return None
    return style.preview_media_id or style.reference_media_id


async def frozen_video_style_prompt(session, project_id: int, owner_id: int, prompt: str):
    """Read the current video style once for both cost preview and job creation."""
    project = await session.get(Project, project_id)
    if not project or not await same_team(session, project.owner_id, owner_id):
        return prompt, None
    selected = str((project.creation_settings or {}).get("style_id", ""))
    if not selected.startswith("preset:") or not selected[7:].isdigit():
        return prompt, None
    style = await session.get(StylePreset, int(selected[7:]))
    if not style or not style.enabled or "video" not in style.modalities:
        return prompt, None
    if style.prompt_suffix:
        prompt += f"\n\n风格要求（{style.name}）：\n{style.prompt_suffix}"
    if style.negative_prompt:
        prompt += f"\n避免：{style.negative_prompt}"
    return prompt, {"id": style.id, "name": style.name,
                    "media_id": style.preview_media_id or style.reference_media_id}


async def apply_project_style(session, job, model):
    if not job.project_id or job.job_type not in {"text", "image", "video"}:
        return None
    if "style_snapshot" in job.payload:
        return job.payload["style_snapshot"]
    project = await session.get(Project, job.project_id)
    if not project or not await same_team(session, project.owner_id, job.owner_id):
        return None
    selected = str((project.creation_settings or {}).get("style_id", ""))
    if not selected.startswith("preset:") or not selected[7:].isdigit():
        return None
    style = await session.get(StylePreset, int(selected[7:]))
    if not style or not style.enabled or job.job_type not in style.modalities:
        return None
    snapshot = {"id": style.id, "name": style.name, "media_id": style.preview_media_id or style.reference_media_id}
    prompt = job.payload["prompt"]
    if style.prompt_suffix:
        prompt += f"\n\n风格要求（{style.name}）：\n{style.prompt_suffix}"
    if job.payload.get("asset_generation_contract") == "character-reference-sheet.v2":
        prompt += (
            "\n\n角色参考资产风格边界：项目风格仅可影响服装设计语言、材质、色彩与渲染质感；"
            "不得引入场景叙事、坐姿、动作表演、戏剧性逆光、浅景深、光晕或背景虚化。"
            "必须维持同一角色的半身面部、全身正面、全身侧面、全身背面四分区排版，"
            "以及完整入镜、均匀影棚光线与浅灰无缝背景。"
            "全身人物高度不得超过画布的82%，头饰上方与鞋底或长袍衣摆下方必须各保留至少8%空白，禁止任何部位贴边或裁切。"
        )
    reference_style_boundaries = {
        "scene-reference.v1": (
            "\n\n场景参考资产风格边界：项目风格不得引入人物、剧情动作、多宫格、浅景深或虚化；"
            "必须保留单一连续空间、稳定透视、深景深、关键结构完整和8%安全边距。"
        ),
        "prop-reference.v1": (
            "\n\n道具参考资产风格边界：项目风格不得引入人物、手持、剧情场景或多宫格；"
            "必须保留单一道具、完整轮廓、均匀影棚光、中性背景和12%四周安全边距。"
        ),
        "costume-reference.v1": (
            "\n\n服装参考资产风格边界：项目风格不得改变已绑定角色的脸型、五官、年龄感、肤色、体型和发型，不得引入动作表演、剧情场景或多宫格；"
            "必须保留同一角色的完整穿着效果、头饰到鞋底全部入镜、均匀影棚光和上下各8%安全边距。"
        ),
        "costume-garment.v1": "\n服装本体优先：只展示衣物，不出现人物、模特或人台；风格图中的人物不能成为画面主体。",
    }
    prompt += reference_style_boundaries.get(str(job.payload.get("asset_generation_contract") or ""), "")
    if job.payload.get("visual_identity_snapshot") and (job.payload.get("parameters") or {}).get("costume_mode") != "garment_only":
        prompt += "\n角色身份与着装优先级：前文已确定的年龄、外貌、人物地域身份、具体上装/下装/鞋履优先于风格要求；不能把长裤改成旗袍，不能用欧美电影风格替换已确定的中国或亚裔身份。"
    if job.job_type in {"text", "video"} and style.negative_prompt:
        prompt += f"\n避免：{style.negative_prompt}"
    negative = "\n".join(filter(None, [job.payload.get("negative_prompt"), style.negative_prompt])) if job.job_type == "image" else job.payload.get("negative_prompt")
    job.payload = {**job.payload, "prompt": prompt, "negative_prompt": negative, "style_snapshot": snapshot}
    return snapshot
