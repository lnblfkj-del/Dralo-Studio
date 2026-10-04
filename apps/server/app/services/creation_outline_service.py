"""M3 分集大纲与剧本业务逻辑：大纲生成、确认、脚本生成与场景分镜拆解。

本模块处理从故事设定到正式分集/场景/分镜的完整链路。
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError
from app.services import outline_management_service, outline_workflow_service

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_STATUS_SUPERSEDED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_SCENE_SHOT_DRAFT,
    ARTIFACT_TYPE_STORY_BIBLE,
    AssetUsage,
    CreationArtifact,
    CreationSession,
    Episode,
    EpisodeProduction,
    Job,
    Project,
    User,
)
from app.schemas.creation import (
    EpisodeOutlineContent,
    EpisodeScriptContent,
    SceneShotDraftContent,
)
from app.services import (
    asset_service,
    job_service,
    project_service,
    script_finalization_service,
    script_version_service,
)
from app.services.creation_session_service import (
    append_message,
    get_artifact,
)
from app.services.episode_duration_policy import episode_duration_guidance
from app.services.creation_script_generation_service import (  # noqa: F401
    apply_episode_script_optimization,
    create_episode_script_generation_jobs,
    create_episode_script_optimization_job,
)
from app.services.narrative_prompt_service import outline_strategy_prompt
from app.services.outline_structure_review_service import review_for_settings
from app.services.story_bible_structure_review import validate_story_bible_structure
from app.services.creation_agent_service import (
    _attach_agent_execution,
    _resolve_agent_execution,
    create_creation_job,
)
from app.services.creation_session_service import (
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    SESSION_STATUS_BREAKDOWN_COMPLETED,
    SESSION_STATUS_BREAKDOWN_GENERATING,
    SESSION_STATUS_BREAKDOWN_REVIEWING,
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_OUTLINE_CONFIRMED,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_SCRIPT_GENERATING,
    SESSION_STATUS_SCRIPT_REVIEWING,
)


async def create_episode_outline_job(
    session: AsyncSession,
    item: CreationSession,
    *,
    expected_story_artifact_id: int | None = None,
    expected_story_revision: int | None = None,
    confirm_story: bool = False,
) -> Job:
    if (expected_story_artifact_id is None) != (expected_story_revision is None):
        raise ConflictError("生成大纲需要同时提供故事设定和修订号")
    from app.services.long_form_workflow import resume_existing
    # Serialize approvals and submissions for the same project on PostgreSQL.
    await session.flush()
    await session.refresh(item, with_for_update=True)
    approval_request = None
    if confirm_story:
        latest = await session.scalar(select(CreationArtifact).where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
            CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
        ).order_by(CreationArtifact.version.desc()).limit(1))
        active = await session.scalar(select(Job).where(
            Job.target_type == ARTIFACT_TYPE_EPISODE_OUTLINE, Job.target_id == item.id,
            Job.owner_id == item.owner_id, Job.status.in_(["queued", "running", "processing", "retrying", "downloading"]),
        ).order_by(Job.id.desc()).limit(1))
        if active is not None and latest is not None:
            parameters = active.payload.get("parameters") or {}
            source = parameters.get("story_source") or {}
            receipt = parameters.get("story_approval_request") or {}
            if (latest.status == ARTIFACT_STATUS_CONFIRMED
                    and latest.id == source.get("artifact_id") == expected_story_artifact_id
                    and latest.revision == source.get("revision")
                    and receipt == {"artifact_id": expected_story_artifact_id, "revision": expected_story_revision}):
                return active
        if latest is None or latest.id != expected_story_artifact_id or latest.revision != expected_story_revision:
            raise ConflictError("故事设定已有变化，请刷新后审核最新内容")
        if latest.status == ARTIFACT_STATUS_DRAFT:
            approval_request = {"artifact_id": latest.id, "revision": latest.revision}
            from app.services.story_narrative_resolution import resolve_story_spec
            item.settings = resolve_story_spec(item.settings, latest.content, item.brief)
            from app.services.creation_story_service import confirm_story_bible
            latest = await confirm_story_bible(session, item, latest.id, latest.revision)
            expected_story_revision = latest.revision
    if expected_story_artifact_id is not None:
        active = await session.scalar(select(Job).where(
            Job.target_type == ARTIFACT_TYPE_EPISODE_OUTLINE, Job.target_id == item.id,
            Job.owner_id == item.owner_id, Job.status.in_(["queued", "running", "processing", "retrying", "downloading"]),
        ).order_by(Job.id.desc()).limit(1))
        if active is not None:
            source = (active.payload.get("parameters") or {}).get("story_source") or {}
            if source.get("artifact_id") == expected_story_artifact_id and source.get("revision") == expected_story_revision:
                return active
            raise ConflictError("分集大纲正在生成，请等待当前任务完成")
    if expected_story_artifact_id is not None:
        expected_story = await get_artifact(session, item, ARTIFACT_TYPE_STORY_BIBLE, status=ARTIFACT_STATUS_CONFIRMED)
        if expected_story.id != expected_story_artifact_id or expected_story.revision != expected_story_revision:
            raise ConflictError("故事设定确认版本已变化，请刷新后重新生成大纲")
    resumed = await resume_existing(session, item, "outline")
    if resumed is not None:
        return resumed
    managed = await session.scalar(select(CreationArtifact).where(CreationArtifact.session_id == item.id, CreationArtifact.artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE).order_by(CreationArtifact.version.desc()).limit(1))
    if managed and (managed.content.get("operation_receipts") or any(entry.get("outline_key") for entry in managed.content.get("episodes", []))):
        raise ConflictError("已有大纲已接入分集管理，请通过 AI 调整提案修改，不能重新生成覆盖分集身份")
    if item.status not in {SESSION_STATUS_CONFIRMED, SESSION_STATUS_OUTLINE_REVIEWING}:
        raise ConflictError("请先确认故事设定")
    bible = await get_artifact(
        session, item, ARTIFACT_TYPE_STORY_BIBLE, status=ARTIFACT_STATUS_CONFIRMED
    )
    validate_story_bible_structure(bible.content, item.settings)
    from app.services.story_narrative_resolution import resolve_story_spec
    confirmed_settings = resolve_story_spec(item.settings, bible.content, item.brief)
    if confirmed_settings != item.settings:
        item.settings = confirmed_settings
        if item.project_id is not None:
            project = await project_service.get_project(session, item.project_id, item.owner_id)
            project_settings = dict(project.creation_settings or {})
            project_settings["narrative_spec"] = confirmed_settings["narrative_spec"]
            project.creation_settings = project_settings
    narrative_spec, structure_strategy = outline_strategy_prompt(item.settings)
    if expected_story_artifact_id is not None and (
        bible.id != expected_story_artifact_id or bible.revision != expected_story_revision
    ):
        raise ConflictError("故事设定确认版本已变化，请刷新后重新生成大纲")
    story_source = {
        "artifact_id": bible.id,
        "version": bible.version,
        "revision": bible.revision,
    }
    episode_count = narrative_spec["episode_count"]
    episode_duration = narrative_spec["episode_duration"]
    duration_guidance = episode_duration_guidance(episode_duration)
    prompt = f"""你是短剧总编剧。根据已确认故事设定生成 {episode_count} 集分集大纲，只输出 JSON，不要 Markdown。
JSON 顶层字段为 episodes；每集字段必须为 number, title, synopsis, dramatic_goal, cliffhanger, characters。
characters 必须是本集实际计划登场的故事设定角色姓名数组，只能使用故事设定中的角色姓名，不得从梗概临时虚构新角色。
每集 synopsis 应交代本集主要事件、冲突与转折、关键登场角色各自的作用及事件结果；dramatic_goal 应明确可执行的本集目标。不要只用一句空泛概括，也不要为凑字数添加无关情节。
逐集对照故事设定和事件脉络规划 characters；梗概中明确参与行动的已设定角色必须列入本集 characters，未参与本集的角色不要机械列入。保持相邻集的因果、人物状态和悬念承接一致。
number 必须从 1 到 {episode_count} 连续排列，episodes 必须恰好 {episode_count} 项；{duration_guidance}
{structure_strategy}
故事设定：{json.dumps(bible.content, ensure_ascii=False)}"""
    job = await create_creation_job(
        session, item, ARTIFACT_TYPE_EPISODE_OUTLINE, prompt, "分集大纲正在生成",
        parameters={
            "story_source": story_source,
            "story_snapshot": bible.content,
            "narrative_spec": narrative_spec,
            **({"story_approval_request": approval_request} if approval_request else {}),
        },
        agent_key="outline", execution_surface="episode_outline",
    )
    from app.services.long_form_workflow import BATCH, configure
    if episode_count > BATCH:
        await configure(session, item, job, "outline")
    item.status = SESSION_STATUS_OUTLINE_GENERATING
    return job


async def create_episode_script_job(session: AsyncSession, item: CreationSession) -> Job:
    if item.status not in {SESSION_STATUS_OUTLINE_CONFIRMED, SESSION_STATUS_SCRIPT_REVIEWING}:
        raise ConflictError("请先确认分集大纲")
    bible = await get_artifact(
        session, item, ARTIFACT_TYPE_STORY_BIBLE, status=ARTIFACT_STATUS_CONFIRMED
    )
    outline = await get_artifact(
        session, item, ARTIFACT_TYPE_EPISODE_OUTLINE, status=ARTIFACT_STATUS_CONFIRMED
    )
    first_episode = outline.content["episodes"][0]
    prompt = f"""你是短剧编剧。根据故事设定与第一集大纲生成第一集完整可拍摄正文，只输出 JSON，不要 Markdown。
JSON 字段必须为 episode_number, title, synopsis, script；episode_number 必须为 1。
script 使用中文剧本格式，明确场景、时间、人物、动作和对白，不要输出分镜。
故事设定：{json.dumps(bible.content, ensure_ascii=False)}
第一集大纲：{json.dumps(first_episode, ensure_ascii=False)}"""
    job = await create_creation_job(
        session, item, ARTIFACT_TYPE_EPISODE_SCRIPT, prompt, "第一集正文正在生成",
        agent_key="script", execution_surface="episode_script",
    )
    item.status = SESSION_STATUS_SCRIPT_GENERATING
    return job


async def update_creation_artifact(
    session: AsyncSession,
    item: CreationSession,
    artifact_id: int,
    artifact_type: str,
    content: dict[str, Any],
    expected_revision: int | None = None,
) -> CreationArtifact:
    artifact = await session.get(CreationArtifact, artifact_id)
    if (
        artifact is None
        or artifact.session_id != item.id
        or artifact.artifact_type != artifact_type
        or artifact.status != ARTIFACT_STATUS_DRAFT
    ):
        raise NotFoundError("可编辑的创作产物不存在")
    if expected_revision is not None and artifact.revision != expected_revision:
        raise ConflictError("大纲已被其他窗口修改，请刷新后重试")
    if artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
        if artifact.content.get("operation_receipts") and expected_revision is None:
            raise ConflictError("分集管理后的保存必须提供版本号")
        content = await outline_management_service.merge_edit(session, item, artifact, content)
        await outline_management_service.cas_write(session, artifact, content, artifact.revision if expected_revision is None else expected_revision)
        await outline_workflow_service.mark_pending(session, item, artifact)
    else:
        artifact.content = content
        artifact.revision += 1
    await session.flush()
    return artifact


async def save_episode_outline_version(
    session: AsyncSession,
    item: CreationSession,
    artifact_id: int,
    content: dict[str, Any],
    expected_revision: int | None = None,
) -> CreationArtifact:
    current = await session.get(CreationArtifact, artifact_id)
    if (
        current is None
        or current.session_id != item.id
        or current.artifact_type != ARTIFACT_TYPE_EPISODE_OUTLINE
        or current.status != ARTIFACT_STATUS_DRAFT
    ):
        raise NotFoundError("可保存的分集大纲不存在")
    if expected_revision is not None and current.revision != expected_revision:
        raise ConflictError("大纲已被其他窗口修改，请刷新后重试")
    if current.content.get("operation_receipts") and expected_revision is None:
        raise ConflictError("分集管理后的保存必须提供版本号")
    validated = await outline_management_service.merge_edit(session, item, current, content)
    story = await outline_management_service._latest_story(session, item)
    validated["structure_review"] = review_for_settings(
        validated,
        item.settings,
        story=story.content if story is not None else None,
    )
    baseline = await outline_management_service.normalize(session, item, current)
    await outline_management_service.cas_write(session, current, baseline, current.revision)
    current.status = ARTIFACT_STATUS_SUPERSEDED
    from sqlalchemy import func
    version = int(await session.scalar(select(func.coalesce(func.max(CreationArtifact.version), 0)).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE,
    ))) + 1
    snapshot = CreationArtifact(
        session_id=item.id,
        artifact_type=ARTIFACT_TYPE_EPISODE_OUTLINE,
        version=version,
        status=ARTIFACT_STATUS_DRAFT,
        content=validated,
        source_job_id=current.source_job_id,
    )
    session.add(snapshot)
    await session.flush()
    await outline_workflow_service.mark_pending(session, item, snapshot)
    item.status = SESSION_STATUS_OUTLINE_REVIEWING
    await append_message(
        session,
        item.id,
        "assistant",
        "outline_version_saved",
        f"分集大纲已保存为 v{version}。",
        parameters={"source_artifact_id": current.id, "version": version},
    )
    await session.flush()
    return snapshot


async def confirm_episode_outline(
    session: AsyncSession,
    item: CreationSession,
    artifact_id: int,
    content: dict[str, Any] | None = None,
    expected_revision: int | None = None,
) -> CreationArtifact:
    # 内容与版本号必须成对出现，否则调用方可能以为做了版本校验而实际没有。
    if (content is None) != (expected_revision is None):
        raise ConflictError("保存并确认需要同时提供内容与版本号")
    if item.status != SESSION_STATUS_OUTLINE_REVIEWING:
        raise ConflictError("当前分集大纲不能确认")
    artifact = await session.get(CreationArtifact, artifact_id)
    if (
        artifact is None
        or artifact.session_id != item.id
        or artifact.artifact_type != ARTIFACT_TYPE_EPISODE_OUTLINE
        or artifact.status != ARTIFACT_STATUS_DRAFT
    ):
        raise NotFoundError("可确认的分集大纲不存在")
    # 原子入口：先按版本校验写入当前编辑内容，再确认，避免确认吞掉未保存的改动。
    if expected_revision is not None:
        if artifact.revision != expected_revision:
            raise ConflictError("大纲已被其他窗口修改，请刷新后重试")
        content = await outline_management_service.merge_edit(session, item, artifact, content)
    else:
        content = await outline_management_service.normalize(session, item, artifact)
    try:
        EpisodeOutlineContent.model_validate(content)
    except ValidationError as exc:
        raise ConflictError("请至少保留一集，并补全每集标题、梗概和戏剧目标后再确认") from exc
    await outline_management_service.validate_links(session, item, content)
    story = await outline_management_service._latest_story(session, item)
    content["structure_review"] = review_for_settings(
        content,
        item.settings,
        story=story.content if story is not None else None,
    )
    await outline_management_service.cas_write(session, artifact, content, artifact.revision)
    artifact.status = ARTIFACT_STATUS_CONFIRMED
    item.status = SESSION_STATUS_OUTLINE_CONFIRMED
    if item.project_id is not None:
        await outline_workflow_service.synchronize(session, item, artifact)
    await session.flush()
    return artifact


async def publish_first_episode(
    session: AsyncSession, item: CreationSession, artifact_id: int, owner: User
) -> Project:
    if item.project_id is not None:
        return await project_service.get_project(session, item.project_id, owner.id)
    if item.status != SESSION_STATUS_SCRIPT_REVIEWING:
        raise ConflictError("请先生成并检查第一集正文")
    bible = await get_artifact(
        session, item, ARTIFACT_TYPE_STORY_BIBLE, status=ARTIFACT_STATUS_CONFIRMED
    )
    outline = await get_artifact(
        session, item, ARTIFACT_TYPE_EPISODE_OUTLINE, status=ARTIFACT_STATUS_CONFIRMED
    )
    script_artifact = await session.get(CreationArtifact, artifact_id)
    if (
        script_artifact is None
        or script_artifact.session_id != item.id
        or script_artifact.artifact_type != ARTIFACT_TYPE_EPISODE_SCRIPT
        or script_artifact.status != ARTIFACT_STATUS_DRAFT
    ):
        raise NotFoundError("可发布的第一集正文不存在")
    script = EpisodeScriptContent.model_validate(script_artifact.content)
    project = await project_service.create_project(session, owner, {
        "name": bible.content["title"],
        "description": bible.content["logline"],
        "genre": bible.content["genre"][:64],
        "creation_settings": item.settings,
    })
    for episode_data in outline.content["episodes"]:
        is_script_episode = episode_data["number"] == script.episode_number
        episode = await project_service.create_episode(session, project, {
            "number": episode_data["number"],
            "title": script.title if is_script_episode else episode_data["title"],
            "synopsis": script.synopsis if is_script_episode else episode_data["synopsis"],
        })
        if is_script_episode:
            await script_version_service.save_script(
                session,
                episode,
                script.script,
                expected=0,
                actor_id=owner.id,
                note="由已确认的 AI 创作会话生成",
                source="ai",
            )
    script_artifact.status = ARTIFACT_STATUS_CONFIRMED
    item.project_id = project.id
    item.status = SESSION_STATUS_COMPLETED
    await session.flush()
    return project


async def get_first_episode(session: AsyncSession, item: CreationSession):
    if item.project_id is None:
        raise ConflictError("请先确认第一集正文并创建项目")
    project = await project_service.get_project(session, item.project_id, item.owner_id)
    episodes = await project_service.list_episodes(session, project.id)
    episode = next((entry for entry in episodes if entry.number == 1), None)
    if episode is None or not (episode.script or "").strip():
        raise ConflictError("第一集正文不存在")
    return project, episode


async def create_scene_shot_draft_job(
    session: AsyncSession, item: CreationSession
) -> Job:
    if item.status not in {SESSION_STATUS_COMPLETED, SESSION_STATUS_BREAKDOWN_REVIEWING}:
        raise ConflictError("请先完成第一集正文")
    _project, episode = await get_first_episode(session, item)
    if await project_service.list_scenes(session, episode.id):
        raise ConflictError("第一集已经包含正式场景，不能用 AI 草稿覆盖")
    prompt = f"""你是短剧分镜师。把第一集剧本拆成可拍摄的场景和分镜，只输出 JSON，不要 Markdown。
JSON 顶层字段为 episode_number 和 scenes，episode_number 必须为 1。
每个 scene 字段为 number, name, location, time_of_day, description, shots。
每个 shot 字段为 number, duration, shot_size, camera_angle, camera_movement, action, dialogue, audio_note, characters。
scene 和 shot 的 number 都必须各自从 1 连续排列；总分镜不超过 200。不要生成图片或视频 Prompt。
第一集剧本：{episode.script}"""
    job = await create_creation_job(
        session, item, ARTIFACT_TYPE_SCENE_SHOT_DRAFT, prompt, "场景分镜正在拆分",
        agent_key="script", execution_surface="scene_shot_draft",
    )
    item.status = SESSION_STATUS_BREAKDOWN_GENERATING
    return job


async def publish_scene_shot_draft(
    session: AsyncSession, item: CreationSession, artifact_id: int
) -> dict[str, int]:
    project, episode = await get_first_episode(session, item)
    artifact = await session.get(CreationArtifact, artifact_id)
    if artifact is None or artifact.session_id != item.id or artifact.artifact_type != ARTIFACT_TYPE_SCENE_SHOT_DRAFT:
        raise NotFoundError("场景分镜草稿不存在")
    existing_scenes = await project_service.list_scenes(session, episode.id)
    if artifact.status == ARTIFACT_STATUS_CONFIRMED and item.status == SESSION_STATUS_BREAKDOWN_COMPLETED:
        shot_count = 0
        for scene in existing_scenes:
            shot_count += len(await project_service.list_shots(session, scene.id))
        return {
            "project_id": project.id,
            "episode_id": episode.id,
            "scene_count": len(existing_scenes),
            "shot_count": shot_count,
        }
    if item.status != SESSION_STATUS_BREAKDOWN_REVIEWING or artifact.status != ARTIFACT_STATUS_DRAFT:
        raise ConflictError("当前场景分镜草稿不能确认")
    if existing_scenes:
        raise ConflictError("第一集已经包含正式场景，不能覆盖现有内容")
    content = SceneShotDraftContent.model_validate(artifact.content)
    if content.episode_number != 1:
        raise ConflictError("场景分镜草稿不属于第一集")
    shot_count = 0
    for scene_data in content.scenes:
        scene = await project_service.create_scene(session, episode, {
            "order": scene_data.number - 1,
            "name": scene_data.name,
            "location": scene_data.location,
            "time_of_day": scene_data.time_of_day or None,
            "description": scene_data.description or None,
        })
        for shot_data in scene_data.shots:
            await project_service.create_shot(session, scene, {
                "order": shot_data.number - 1,
                "duration": shot_data.duration,
                "shot_size": shot_data.shot_size,
                "camera_angle": shot_data.camera_angle or None,
                "camera_movement": shot_data.camera_movement or None,
                "action": shot_data.action,
                "dialogue": shot_data.dialogue or None,
                "audio_note": shot_data.audio_note or None,
                "refs": {"characters": shot_data.characters},
            })
            shot_count += 1
    artifact.status = ARTIFACT_STATUS_CONFIRMED
    item.status = SESSION_STATUS_BREAKDOWN_COMPLETED
    await session.flush()
    return {
        "project_id": project.id,
        "episode_id": episode.id,
        "scene_count": len(content.scenes),
        "shot_count": shot_count,
    }


async def create_episode_scene_shot_proposal_job(
    session: AsyncSession,
    episode: Episode,
) -> Job:
    from app.services.episode_preparation_gate import require_preparation
    from app.services.episode_empty_scenes import empty_placeholders
    await require_preparation(session, episode)
    source = (episode.script or "").strip()
    if not source:
        raise ConflictError("请先导入、编写或确认本集剧本")
    await empty_placeholders(session, episode)
    active = await session.scalar(select(Job.id).where(
        Job.owner_id == episode.owner_id,
        Job.target_type == JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
        Job.target_id == episode.id,
        Job.status.in_([
            JOB_STATUS_QUEUED,
            JOB_STATUS_RUNNING,
            JOB_STATUS_PROCESSING,
            JOB_STATUS_RETRYING,
        ]),
    ))
    if active is not None:
        raise ConflictError("本集场景分镜正在拆解")

    production = await project_service.get_episode_production(session, episode)
    selected_asset_ids = [int(value) for value in production["settings"].get("asset_ids", [])]
    unique_asset_ids = list(dict.fromkeys(selected_asset_ids))
    assets_by_id = await asset_service.get_project_assets(
        session, episode.project_id, unique_asset_ids
    )
    if len(assets_by_id) != len(unique_asset_ids):
        raise NotFoundError("资产不存在")
    asset_catalog: list[dict[str, Any]] = []
    for asset_id in unique_asset_ids:
        asset = assets_by_id[asset_id]
        asset_catalog.append({
            "id": asset.id,
            "type": asset.asset_type,
            "name": asset.name,
            "description": asset.description or "",
        })

    model, execution, instruction_prefix = await _resolve_agent_execution(
        session, "script", "episode_scene_shot_breakdown"
    )
    prompt = f"""{instruction_prefix}

你是短剧分镜师。把第 {episode.number} 集剧本拆成可拍摄的场景和分镜，只输出 JSON，不要 Markdown。
JSON 顶层字段为 episode_number 和 scenes，episode_number 必须为 {episode.number}。
每个 scene 字段为 number, name, location, time_of_day, description, shots。
每个 shot 字段为 number, duration, shot_size, camera_angle, camera_movement, action, dialogue, audio_note, characters, asset_ids。
scene 和 shot 的 number 都必须各自从 1 连续排列；总分镜不超过 200；不要生成图片或视频 Prompt。
asset_ids 只能从“本集已绑定资产”中选择与该分镜实际有关的编号，没有则返回空数组。
本集已绑定资产：{json.dumps(asset_catalog, ensure_ascii=False)}
本集剧本：{source}"""
    job = await job_service.create_text_job(
        session,
        episode.owner_id,
        provider_model_id=model.id,
        prompt=prompt,
        project_id=episode.project_id,
        parameters={
            "agent_workspace": "episode_scene_shot_breakdown",
            "source_revision": episode.script_revision,
            "source_script": episode.script or "",
            "asset_ids": selected_asset_ids,
        },
    )
    job.target_type = JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL
    job.target_id = episode.id
    _attach_agent_execution(job, execution)
    await session.flush()
    return job


async def apply_episode_scene_shot_proposal(
    session: AsyncSession,
    episode: Episode,
    job_id: int,
    expected_script_revision: int,
    content: dict[str, Any],
    actor_id: int,
) -> dict[str, int]:
    from app.services.episode_preparation_gate import require_preparation
    from app.services.episode_empty_scenes import empty_placeholders
    await require_preparation(session, episode)
    job = await job_service.get_job(session, job_id, actor_id)
    if (
        job.project_id != episode.project_id
        or job.target_type != JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL
        or job.target_id != episode.id
        or job.status != "succeeded"
    ):
        raise ConflictError("场景分镜提案尚未完成或不属于本集")
    result = dict(job.result or {})
    preview = dict(result.get("action_preview") or {})
    if preview.get("status") == "applied":
        return dict(result.get("apply_result") or {})
    if preview.get("status") == "rejected":
        raise ConflictError("该场景分镜提案已放弃")
    source_revision = int(dict(preview.get("source") or {}).get("revision", -1))
    if source_revision != expected_script_revision or episode.script_revision != expected_script_revision:
        raise ConflictError("本集剧本已更新，请基于最新版本重新拆分")
    placeholders = await empty_placeholders(session, episode)

    draft = SceneShotDraftContent.model_validate(content)
    if draft.episode_number != episode.number:
        raise ConflictError("场景分镜提案不属于当前分集")
    allowed_asset_ids = {
        int(value) for value in dict(job.payload.get("parameters") or {}).get("asset_ids", [])
    }
    referenced_asset_ids = {
        asset_id
        for scene_data in draft.scenes
        for shot_data in scene_data.shots
        for asset_id in shot_data.asset_ids
    }
    if not referenced_asset_ids.issubset(allowed_asset_ids):
        raise ConflictError("提案引用了未绑定到本集的资产")
    assets = await asset_service.get_project_assets(
        session, episode.project_id, list(referenced_asset_ids)
    )
    if len(assets) != len(referenced_asset_ids):
        raise NotFoundError("资产不存在")

    for placeholder in placeholders:
        await project_service.delete_scene(session, placeholder)
    shot_count = 0
    asset_usage_count = 0
    for scene_data in draft.scenes:
        scene = await project_service.create_scene(session, episode, {
            "order": scene_data.number - 1,
            "name": scene_data.name,
            "location": scene_data.location,
            "time_of_day": scene_data.time_of_day or None,
            "description": scene_data.description or None,
        })
        for shot_data in scene_data.shots:
            shot = await project_service.create_shot(session, scene, {
                "order": shot_data.number - 1,
                "duration": shot_data.duration,
                "shot_size": shot_data.shot_size,
                "camera_angle": shot_data.camera_angle or None,
                "camera_movement": shot_data.camera_movement or None,
                "action": shot_data.action,
                "dialogue": shot_data.dialogue or None,
                "audio_note": shot_data.audio_note or None,
                "refs": {
                    "characters": shot_data.characters,
                    "asset_ids": shot_data.asset_ids,
                },
            })
            for asset_id in shot_data.asset_ids:
                session.add(AssetUsage(
                    project_id=episode.project_id,
                    asset_id=asset_id,
                    episode_id=episode.id,
                    scene_id=scene.id,
                    shot_id=shot.id,
                    usage_type=assets[asset_id].asset_type,
                ))
                asset_usage_count += 1
            shot_count += 1

    apply_result = {
        "project_id": episode.project_id,
        "episode_id": episode.id,
        "scene_count": len(draft.scenes),
        "shot_count": shot_count,
        "asset_usage_count": asset_usage_count,
    }
    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is not None:
        production.source_script_revision = episode.script_revision
        production.script_stale = False
        production.script_stale_reason = None
    preview.update({"status": "applied", **apply_result})
    job.result = {**result, "action_preview": preview, "apply_result": apply_result}
    await session.flush()
    return apply_result


async def restore_artifact_version(
    session: AsyncSession,
    item: CreationSession,
    artifact_id: int,
    artifact_type: str,
    expected_current_id: int | None = None,
    expected_revision: int | None = None,
) -> CreationArtifact:
    source = await session.get(CreationArtifact, artifact_id)
    if source is None or source.session_id != item.id or source.artifact_type != artifact_type:
        raise NotFoundError("要恢复的版本不存在")
    if expected_current_id is not None or expected_revision is not None:
        from sqlalchemy import update
        latest = await session.scalar(select(CreationArtifact).where(CreationArtifact.session_id == item.id, CreationArtifact.artifact_type == artifact_type).order_by(CreationArtifact.version.desc()).limit(1))
        if latest.id != expected_current_id or latest.revision != expected_revision:
            raise ConflictError("当前版本已变化，请刷新后重新选择恢复版本")
        locked = await session.execute(update(CreationArtifact).where(CreationArtifact.id == latest.id, CreationArtifact.revision == expected_revision).values(revision=expected_revision + 1).execution_options(synchronize_session=False))
        if locked.rowcount != 1:
            raise ConflictError("当前内容已变化，恢复操作已取消")
        await session.refresh(latest)
    drafts = list((await session.execute(select(CreationArtifact).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == artifact_type,
        CreationArtifact.status == ARTIFACT_STATUS_DRAFT,
    ))).scalars())
    for draft in drafts:
        draft.status = ARTIFACT_STATUS_SUPERSEDED
    from sqlalchemy import func
    version = int(await session.scalar(select(func.coalesce(func.max(CreationArtifact.version), 0)).where(
        CreationArtifact.session_id == item.id,
        CreationArtifact.artifact_type == artifact_type,
    ))) + 1
    restored = CreationArtifact(
        session_id=item.id,
        artifact_type=artifact_type,
        version=version,
        status=ARTIFACT_STATUS_DRAFT,
        content=(await outline_workflow_service.restore_content(session, item, source)) if artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE else source.content,
        source_job_id=source.source_job_id,
    )
    session.add(restored)
    await session.flush()
    if artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
        await outline_workflow_service.mark_pending(session, item, restored)
    if artifact_type == ARTIFACT_TYPE_STORY_BIBLE:
        downstream = await session.scalar(select(CreationArtifact.id).where(
            CreationArtifact.session_id == item.id,
            CreationArtifact.artifact_type == ARTIFACT_TYPE_EPISODE_OUTLINE,
            CreationArtifact.status != ARTIFACT_STATUS_SUPERSEDED,
        ).limit(1))
        if downstream is None:
            item.status = SESSION_STATUS_REVIEWING
    else:
        item.status = SESSION_STATUS_OUTLINE_REVIEWING
    await append_message(
        session, item.id, "assistant", "version_restore",
        f"已将 {artifact_type} v{source.version} 恢复为 v{version} 草稿。",
    )
    await session.flush()
    return restored


# 需要从原文件导入的辅助函数
from app.models import (
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    SESSION_STATUS_CONFIRMED,
    SESSION_STATUS_OUTLINE_GENERATING,
    SESSION_STATUS_REVIEWING,
)
