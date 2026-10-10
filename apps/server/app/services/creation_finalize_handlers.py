"""Non-asset success handlers for creation job finalization."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AppError,
    ConflictError,
    ModelOutputBusinessValidationError,
    ModelOutputPollutedError,
    NotFoundError,
)
from app.models import (
    ARTIFACT_STATUS_CONFIRMED,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    ARTIFACT_TYPE_EPISODE_SCRIPT,
    ARTIFACT_TYPE_STORY_BIBLE,
    SESSION_STATUS_BREAKDOWN_REVIEWING,
    SESSION_STATUS_OUTLINE_REVIEWING,
    SESSION_STATUS_REVIEWING,
    SESSION_STATUS_SCRIPT_GENERATING,
    SESSION_STATUS_SCRIPT_REVIEWING,
    CreationArtifact,
    CreationSession,
    Episode,
    Job,
)
from app.schemas.creation import (
    CreativeDirectionResult,
    CharacterBatchAgentResult,
    EpisodeOutlineContent,
    EpisodeScriptContent,
    EpisodeScriptOptimizationResult,
    OutlineAgentResult,
    SceneShotDraftContent,
    UploadedScriptOptimizationResult,
)
from app.services import script_version_service
from app.services.creation_extraction_service import (
    finalize_script_story_extraction,
    finalize_script_study_batch,
)
from app.services.creation_finalize_service import _repair_creative_direction_understanding
from app.services.creation_session_service import (
    JOB_TARGET_CREATIVE_DIRECTION,
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_OUTLINE_AGENT,
    JOB_TARGET_SCRIPT_IMPORT,
    JOB_TARGET_SCRIPT_STORY_EXTRACTION,
    JOB_TARGET_SCRIPT_STUDY_BATCH,
    append_message,
    create_artifact,
    parse_structured_result,
)
from app.services.creation_story_service import parse_story_bible
from app.services.episode_script_structure_review import (
    review_episode_script_structure,
    structure_review_reason,
)
from app.services.outline_structure_review_service import review_for_settings


async def finalize_episode_job(session: AsyncSession, job: Job, result: dict[str, Any]) -> None:
    if job.target_type == JOB_TARGET_EPISODE_SCRIPT_GENERATION:
        from app.services.creation_script_generation_service import (
            episode_script_length_limits,
            repair_episode_script_result,
            validate_episode_script_job_sources,
        )

        episode = await validate_episode_script_job_sources(
            session, job, require_parent_active=True
        )
        try:
            generated = parse_structured_result(
                str(result.get("text", "")), EpisodeScriptContent,
                "单集剧本生成结果", repair=repair_episode_script_result,
            )
        except ModelOutputPollutedError:
            from app.services.structured_output_service import recover_unique_structured_result
            generated = recover_unique_structured_result(
                str(result.get("text", "")), EpisodeScriptContent, "单集剧本生成结果",
                repair=repair_episode_script_result,
                accepts=lambda value: value["episode_number"] == episode.number,
            )
            result["structured_response_recovery"] = {"strategy": "unique_valid_document", "model_called": False}
        if generated["episode_number"] != episode.number:
            raise ModelOutputBusinessValidationError(
                f"模型返回第 {generated['episode_number']} 集，预期为第 {episode.number} 集",
                details={
                    "actual_episode_numbers": [generated["episode_number"]],
                    "expected_episode_numbers": [episode.number],
                },
            )
        parameters = dict(job.payload.get("parameters") or {})
        limits = parameters.get("script_length_budget") or episode_script_length_limits(
            parameters.get("target_duration_seconds")
        )
        parameters["script_length_budget"] = limits
        script_chars = len(str(generated["script"]).strip())
        recommended_maximum = int(limits.get("maximum_chars") or 0) if limits else 0
        result["script_length_validation"] = {
            "target_duration_seconds": limits["seconds"] if limits else None,
            "script_chars": script_chars,
            "recommended_minimum_chars": limits["minimum_chars"] if limits else None,
            "recommended_maximum_chars": limits["maximum_chars"] if limits else None,
            "hard_maximum_chars": None,
            "status": "warning" if recommended_maximum and script_chars > round(recommended_maximum * 1.25) else "passed",
        }
        structure_review = review_episode_script_structure(generated, parameters)
        result["structure_quality_review"] = structure_review
        expected_revision = int(parameters.get("source_revision", episode.script_revision))
        updated = await script_version_service.save_script(
            session,
            episode,
            generated["script"],
            expected=expected_revision,
            actor_id=job.owner_id,
            note="AI 生成分集正文",
            source="ai",
        )
        updated.title = generated["title"]
        updated.synopsis = generated["synopsis"]
        review_reason = structure_review_reason(structure_review)
        if review_reason:
            updated.continuity_review_status = "warning"
            updated.continuity_review_reason = review_reason
        from app.services.story_continuity_service import sync_episode_continuity_records

        await sync_episode_continuity_records(
            session,
            updated,
            confirmation_status="draft",
            known_character_names=list(parameters.get("known_character_names") or []),
            continuity_update=generated["continuity_update"],
        )
        workflow_session = None
        creation_session_id = int(parameters.get("session_id") or 0)
        if creation_session_id:
            workflow_session = await session.get(CreationSession, creation_session_id)
            if workflow_session is not None and workflow_session.owner_id == job.owner_id:
                await append_message(
                    session,
                    workflow_session.id,
                    "assistant",
                    "episode_script_generated",
                    f"第 {episode.number} 集正文已生成并保存为 R{updated.script_revision}。",
                    job_id=job.id,
                )
        result["continuity_update"] = generated["continuity_update"]
        result["applied_revision"] = updated.script_revision
        if workflow_session is not None:
            from app.services.creation_script_generation_service import (
                queue_next_episode_script_job,
            )

            # Flush this episode outside the savepoint. Only successor preparation rolls back.
            await session.flush()
            try:
                async with session.begin_nested():
                    next_job = await queue_next_episode_script_job(
                        session, workflow_session, job, generated["continuity_update"],
                    )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("分集正文已保存，下一集准备失败")
                reason = exc.message if isinstance(exc, AppError) else f"系统准备异常（{type(exc).__name__}）"
                result["next_episode_preparation_error"] = {
                    "message": reason, "completed_episode": generated["episode_number"],
                }
                await session.refresh(job)
                await session.refresh(workflow_session)
                if job.parent_job_id:
                    parent = await session.get(Job, job.parent_job_id)
                    if parent is not None:
                        await session.refresh(parent)
                next_job = None
            workflow_session.status = (
                SESSION_STATUS_SCRIPT_GENERATING
                if next_job is not None
                else SESSION_STATUS_SCRIPT_REVIEWING
            )
        return
    if job.target_type == JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION:
        episode = await session.get(Episode, job.target_id)
        if episode is None or episode.owner_id != job.owner_id:
            raise NotFoundError("分集不存在")
        proposal = parse_structured_result(
            str(result.get("text", "")),
            EpisodeScriptOptimizationResult,
            "单集剧本优化结果",
        )
        parameters = dict(job.payload.get("parameters") or {})
        proposal_data = {
            **proposal,
            "episode_id": episode.id,
            "source_revision": int(parameters.get("source_revision", episode.script_revision)),
            "source_script": str(parameters.get("source_script", episode.script or "")),
        }
        result["proposal"] = proposal_data
        from app.services.screenplay_review import review_sources
        source_review = review_sources(proposal["script"], parameters.get("screenplay_source_catalog") or [])
        result["screenplay_source_review"] = {key: value for key, value in source_review.items() if key != "records"}
        result["action_preview"] = {
            "kind": "episode_script",
            "status": "pending",
            "title": f"第 {episode.number} 集剧本优化",
            "summary": proposal["reply"],
            "target_type": ARTIFACT_TYPE_EPISODE_SCRIPT,
            "source": {
                "id": episode.id,
                "revision": proposal_data["source_revision"],
                "title": episode.title or "",
                "synopsis": episode.synopsis or "",
                "script": proposal_data["source_script"],
            },
            "proposed": {
                "title": proposal["title"],
                "synopsis": proposal["synopsis"],
                "script": proposal["script"],
            },
            "trace": {
                "job_id": job.id,
                "provider": job.provider,
                "model": job.model,
                "model_id": job.payload.get("provider_model_id"),
                "agent_workspace": parameters.get("agent_workspace"),
            },
        }
        return
    if job.target_type == JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL:
        episode = await session.get(Episode, job.target_id)
        if episode is None or episode.owner_id != job.owner_id:
            raise NotFoundError("分集不存在")
        proposal = parse_structured_result(
            str(result.get("text", "")),
            SceneShotDraftContent,
            "单集场景分镜拆解结果",
        )
        if proposal["episode_number"] != episode.number:
            raise ModelOutputBusinessValidationError(
                f"模型返回第 {proposal['episode_number']} 集，预期为第 {episode.number} 集",
                details={
                    "actual_episode_numbers": [proposal["episode_number"]],
                    "expected_episode_numbers": [episode.number],
                },
            )
        parameters = dict(job.payload.get("parameters") or {})
        source_revision = int(parameters.get("source_revision", episode.script_revision))
        result["proposal"] = proposal
        result["action_preview"] = {
            "kind": "episode_scene_shot",
            "status": "pending",
            "title": f"第 {episode.number} 集场景分镜拆解",
            "summary": (
                f"已生成 {len(proposal['scenes'])} 个场景、"
                f"{sum(len(scene['shots']) for scene in proposal['scenes'])} 个分镜；确认前不会写入正式结构。"
            ),
            "target_type": JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
            "source": {
                "id": episode.id,
                "revision": source_revision,
                "script": str(parameters.get("source_script", episode.script or "")),
            },
            "proposed": proposal,
            "trace": {
                "job_id": job.id,
                "provider": job.provider,
                "model": job.model,
                "model_id": job.payload.get("provider_model_id"),
                "agent_workspace": parameters.get("agent_workspace"),
            },
        }
        return


async def finalize_session_job(
    session: AsyncSession, item: CreationSession, job: Job, result: dict[str, Any]
) -> None:
    if (job.payload.get("parameters") or {}).get("long_form_work_id"):
        from app.services.long_form_workflow import advance

        if await advance(session, item, job, result):
            return
    if job.target_type == "outline_continuation":
        from app.services.outline_continuation_service import finalize

        await finalize(session, item, job, result)
        return
    if job.target_type == JOB_TARGET_CREATIVE_DIRECTION:
        parameters = dict(job.payload.get("parameters") or {})
        input_snapshot = dict(parameters.get("input_snapshot") or {})
        repaired_fields: list[str] = []
        direction_result = parse_structured_result(
            str(result.get("text", "")),
            CreativeDirectionResult,
            "故事方向生成结果",
            repair=lambda payload: _repair_creative_direction_understanding(
                payload, input_snapshot, repaired_fields
            ),
        )
        settings = dict(item.settings or {})
        workflow = dict(settings.get("creative_workflow") or {})
        batch = {
            "status": "completed",
            "job_id": job.id,
            "version": int(parameters.get("proposal_version") or 1),
            "fingerprint": str(parameters.get("input_fingerprint") or ""),
            "input_snapshot": dict(parameters.get("input_snapshot") or {}),
            "understanding": direction_result["understanding"],
            "proposals": direction_result["proposals"],
            "execution": dict(job.payload.get("agent_execution") or {}),
        }
        if repaired_fields:
            batch["validation_repairs"] = repaired_fields
            result["validation_repairs"] = repaired_fields
        workflow["direction_proposals"] = batch
        workflow["direction_proposal_request"] = {
            "status": "completed",
            "job_id": job.id,
            "fingerprint": batch["fingerprint"],
            "version": batch["version"],
        }
        settings["creative_workflow"] = workflow
        item.settings = settings
        result["direction_proposals"] = batch
        await append_message(
            session,
            item.id,
            "assistant",
            "direction_proposals",
            "已生成三个可审阅的专业故事方向。",
            job_id=job.id,
            parameters={"fingerprint": batch["fingerprint"], "version": batch["version"]},
        )
        await session.flush()
        return
    if job.target_type == JOB_TARGET_SCRIPT_STUDY_BATCH:
        await finalize_script_study_batch(session, item, job, result)
        return
    if job.target_type == JOB_TARGET_SCRIPT_STORY_EXTRACTION:
        await finalize_script_story_extraction(session, item, job, result)
        return
    if job.target_type == JOB_TARGET_OUTLINE_AGENT:
        parameters = dict(job.payload.get("parameters") or {})
        agent_result = parse_structured_result(
            str(result.get("text", "")),
            CharacterBatchAgentResult if parameters.get("character_batch_completion") else OutlineAgentResult,
            "角色批量补全" if parameters.get("character_batch_completion") else "Agent 修改结果",
        )
        proposed_type = None
        proposed_content = None
        if agent_result.get("story_bible") is not None:
            proposed_type = ARTIFACT_TYPE_STORY_BIBLE
            proposed_content = agent_result["story_bible"]
        elif agent_result.get("episode_outline") is not None:
            proposed_type = ARTIFACT_TYPE_EPISODE_OUTLINE
            proposed_content = agent_result["episode_outline"]
        options = agent_result.get("story_options")
        scope = parameters.get("story_adjustment")
        if options and not scope:
            raise ConflictError("当前入口不支持多个故事方案")
        if scope and not parameters.get("story_sync"):
            from app.services.story_planning_service import OVERVIEW_FIELDS, restrict_story_section
            if scope["section"] == "overview" and not options:
                raise ConflictError("AI 未返回三个概览方案，请重新生成；原内容未修改")
            if options:
                if parameters.get("story_options_version") == 2 and (
                    agent_result.get("recommended_option_index") is None
                    or not agent_result.get("recommendation_reason", "").strip()
                    or any(not all(option.get(key) for key in ("direction", "highlight", "risk", "change_scope")) for option in options)
                ):
                    raise ConflictError("AI 未提供完整对比和推荐，请重新生成；原故事未修改")
                options = [{key: option.get(key) for key in (
                    *OVERVIEW_FIELDS, "direction", "highlight", "risk", "change_scope",
                )} for option in options]
                proposed_type = ARTIFACT_TYPE_STORY_BIBLE
                proposed_content = options[0]
            elif proposed_type is None:
                raise ConflictError("AI 只返回了文字建议，未生成事件修改方案；原内容未修改")
        if proposed_type is not None:
            from app.services.story_planning_service import (
                preserve_story_fields,
                restrict_batch_completion,
            )

            if parameters.get("character_batch_completion"):
                if proposed_type != ARTIFACT_TYPE_STORY_BIBLE:
                    raise ConflictError("批量角色补全不能修改分集大纲")
                proposed_content, batch_report = restrict_batch_completion(
                    parameters["character_batch_completion"], proposed_content,
                    allow_empty=bool(parameters.get("long_form_work_id")),
                )
            elif parameters.get("story_sync"):
                if proposed_type != ARTIFACT_TYPE_STORY_BIBLE:
                    raise ConflictError("联动调整必须返回完整故事设定")
                from app.services.story_adjustment_workflow import validate_synced_story
                proposed_content = validate_synced_story(parameters, proposed_content, item.settings)
            elif parameters.get("story_adjustment"):
                if proposed_type != ARTIFACT_TYPE_STORY_BIBLE:
                    raise ConflictError("故事资料页面调整不能修改分集大纲")
                from app.services.story_planning_service import restrict_story_section
                proposed_content = restrict_story_section(
                    parameters["story_adjustment"], proposed_content, item.settings
                )
            elif parameters.get("character_outline_coverage"):
                if proposed_type != ARTIFACT_TYPE_EPISODE_OUTLINE:
                    raise ConflictError("角色覆盖修订不能修改故事设定")
                from app.services.outline_character_coverage import restrict_outline_coverage

                proposed_content = restrict_outline_coverage(
                    parameters["character_outline_coverage"], proposed_content
                )
            elif proposed_type == ARTIFACT_TYPE_STORY_BIBLE and parameters.get("story_snapshot"):
                proposed_content = preserve_story_fields(
                    parameters["story_snapshot"], proposed_content
                )
            source = dict((parameters.get("source_artifacts") or {}).get(proposed_type) or {})
            result["action_preview"] = {
                "kind": "creation_artifact",
                "status": "pending",
                "title": (
                    "全角色批量补全"
                    if parameters.get("character_batch_completion")
                    else "故事概览调整"
                    if (parameters.get("story_adjustment") or {}).get("section") == "overview"
                    else "事件脉络调整"
                    if (parameters.get("story_adjustment") or {}).get("section") == "events"
                    else "分集角色覆盖修订"
                    if parameters.get("character_outline_coverage")
                    else "故事设定调整"
                    if proposed_type == ARTIFACT_TYPE_STORY_BIBLE
                    else "分集大纲调整"
                ),
                "summary": agent_result["reply"],
                "target_type": proposed_type,
                **({"story_section": parameters["story_adjustment"]["section"]} if parameters.get("story_adjustment") else {}),
                "source": {
                    "id": source.get("id"),
                    "version": int(source.get("version") or 0),
                    "revision": source.get("revision"),
                },
                "proposed": proposed_content,
                **({"options": options,
                    "recommended_option_index": agent_result.get("recommended_option_index"),
                    "recommendation_reason": agent_result.get("recommendation_reason", "")} if options else {}),
                **(
                    {
                        "character_batch": {
                            "items": batch_report,
                            "requested_count": len(
                                parameters["character_batch_completion"]["targets"]
                            ),
                        }
                    }
                    if parameters.get("character_batch_completion")
                    else {}
                ),
                "trace": {
                    "job_id": job.id,
                    "provider": job.provider,
                    "model": job.model,
                    "model_id": job.payload.get("provider_model_id"),
                    "skill_id": parameters.get("skill_id"),
                    "skill_key": parameters.get("skill_key"),
                    "skill_version": parameters.get("skill_instruction_version"),
                    "source_reference_version": parameters.get("source_reference_version"),
                    "agent_workspace": parameters.get("agent_workspace"),
                },
            }
        if parameters.get("story_sync"):
            if proposed_type != ARTIFACT_TYPE_STORY_BIBLE:
                raise ConflictError("AI 未返回完整故事，原内容未修改")
            from app.services.story_adjustment_workflow import finalize_story_sync
            await finalize_story_sync(session, item, job, result)
            await session.flush()
            return
        reply = agent_result["reply"]
        await append_message(
            session,
            item.id,
            "assistant",
            "agent_reply",
            (
                f"{reply}（已生成待审阅提案，确认前不会修改正式内容）"
                if proposed_type is not None
                else f"{reply}（未修改结构化内容）"
            ),
            job_id=job.id,
            parameters={**parameters, "action_preview_job_id": job.id if proposed_type else None},
        )
        await session.flush()
        return
    if job.target_type == JOB_TARGET_SCRIPT_IMPORT:
        result_schema = UploadedScriptOptimizationResult
        result_label = (
            "上传剧本优化结果" if job.target_type == JOB_TARGET_SCRIPT_IMPORT else "Agent 修改结果"
        )
        agent_result = parse_structured_result(
            str(result.get("text", "")), result_schema, result_label
        )
        changed = False
        if agent_result.get("story_bible") is not None:
            await create_artifact(
                session,
                item,
                job,
                ARTIFACT_TYPE_STORY_BIBLE,
                agent_result["story_bible"],
                "已生成可编辑的故事设定。",
            )
            item.status = SESSION_STATUS_REVIEWING
            changed = True
        if agent_result.get("episode_outline") is not None:
            expected_count = int(item.settings.get("episode_count") or 1)
            outline_count = len(agent_result["episode_outline"]["episodes"])
            if job.target_type == JOB_TARGET_SCRIPT_IMPORT and outline_count != expected_count:
                raise ModelOutputBusinessValidationError(
                    f"AI 返回了 {outline_count} 集分集大纲，结构化解析结果为 {expected_count} 集，请重试"
                )
            await create_artifact(
                session,
                item,
                job,
                ARTIFACT_TYPE_EPISODE_OUTLINE,
                agent_result["episode_outline"],
                "已生成可编辑的分集规划。",
            )
            item.status = SESSION_STATUS_OUTLINE_REVIEWING
            changed = True
        reply = agent_result["reply"]
        await append_message(
            session,
            item.id,
            "assistant",
            "agent_reply",
            reply if changed else f"{reply}（未修改结构化内容）",
            job_id=job.id,
            parameters=dict(job.payload.get("parameters") or {}),
        )
        if job.target_type == JOB_TARGET_SCRIPT_IMPORT:
            settings = dict(item.settings)
            settings["script_study"] = {
                "status": "completed",
                "detected_episode_count": int(settings.get("episode_count") or 1),
                "job_id": job.id,
            }
            item.settings = settings
        await session.flush()
        return
    if job.target_type == ARTIFACT_TYPE_STORY_BIBLE:
        content = parse_story_bible(str(result.get("text", "")))
        next_status = SESSION_STATUS_REVIEWING
        message = "故事设定已生成，请查看并确认。"
        settings = dict(item.settings or {})
        workflow = dict(settings.get("creative_workflow") or {})
        if workflow.get("stage") == "generating_story_bible":
            workflow["stage"] = "story_bible_ready"
            workflow["story_bible_job_id"] = job.id
            settings["creative_workflow"] = workflow
            item.settings = settings
    elif job.target_type == ARTIFACT_TYPE_EPISODE_OUTLINE:
        content = parse_structured_result(
            str(result.get("text", "")), EpisodeOutlineContent, "分集大纲"
        )
        parameters = dict(job.payload.get("parameters") or {})
        story_source = dict(parameters.get("story_source") or {})
        story_snapshot = parameters.get("story_snapshot")
        if story_source:
            story = await session.get(CreationArtifact, story_source.get("artifact_id"))
            if (
                story is None
                or story.session_id != item.id
                or story.artifact_type != ARTIFACT_TYPE_STORY_BIBLE
                or story.status != ARTIFACT_STATUS_CONFIRMED
                or story.version != story_source.get("version")
                or story.revision != story_source.get("revision")
            ):
                raise ConflictError(
                    "故事设定已更新，本次大纲结果已过期，请基于最新确认版本重新生成"
                )
        else:
            story = await session.scalar(
                select(CreationArtifact)
                .where(
                    CreationArtifact.session_id == item.id,
                    CreationArtifact.artifact_type == ARTIFACT_TYPE_STORY_BIBLE,
                    CreationArtifact.status == ARTIFACT_STATUS_CONFIRMED,
                )
                .order_by(CreationArtifact.version.desc())
                .limit(1)
            )
            if story is not None:
                story_source = {
                    "artifact_id": story.id,
                    "version": story.version,
                    "revision": story.revision,
                }
                story_snapshot = story.content
        if story_snapshot is not None:
            from app.services.outline_character_coverage import canonicalize_outline

            content = canonicalize_outline(story_snapshot, content)
        content["story_source"] = story_source
        content["structure_review"] = review_for_settings(
            content,
            item.settings,
            story=story_snapshot if isinstance(story_snapshot, dict) else None,
        )
        next_status = SESSION_STATUS_OUTLINE_REVIEWING
        message = "分集大纲已生成，请逐集检查并确认。"
    elif job.target_type == ARTIFACT_TYPE_EPISODE_SCRIPT:
        content = parse_structured_result(
            str(result.get("text", "")), EpisodeScriptContent, "第一集正文"
        )
        if content["episode_number"] != 1:
            raise ModelOutputBusinessValidationError(
                "模型返回的不是第一集正文，请重新生成",
                details={
                    "actual_episode_numbers": [content["episode_number"]],
                    "expected_episode_numbers": [1],
                },
            )
        next_status = SESSION_STATUS_SCRIPT_REVIEWING
        message = "第一集正文已生成，请编辑确认后创建项目。"
    else:
        content = parse_structured_result(
            str(result.get("text", "")), SceneShotDraftContent, "场景分镜草稿"
        )
        if content["episode_number"] != 1:
            raise ModelOutputBusinessValidationError(
                "模型返回的不是第一集场景分镜，请重新生成",
                details={
                    "actual_episode_numbers": [content["episode_number"]],
                    "expected_episode_numbers": [1],
                },
            )
        next_status = SESSION_STATUS_BREAKDOWN_REVIEWING
        message = "第一集场景和分镜草稿已生成，请检查后确认写入。"
    await create_artifact(session, item, job, job.target_type, content, message)
    item.status = next_status
    await session.flush()
