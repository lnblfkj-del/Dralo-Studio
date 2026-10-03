"""Generate a selected story direction and atomically adopt the complete result."""

import json
from copy import deepcopy

from sqlalchemy import update

from app.core.errors import ConflictError
from app.models import Job
from app.schemas.creation import StoryBibleContent
from app.services.story_planning_service import OVERVIEW_FIELDS, preserve_story_fields


async def start_story_sync(session, item, parent, preview, selected_index):
    from app.services.creation_agent_service import create_creation_job
    from app.services.creation_session_service import JOB_TARGET_OUTLINE_AGENT, append_message
    from app.services.narrative_prompt_service import story_bible_strategy_prompt

    choice = {key: deepcopy(preview["options"][selected_index][key]) for key in OVERVIEW_FIELDS}
    parameters = dict(parent.payload.get("parameters") or {})
    baseline = parameters.get("story_snapshot") or (parameters.get("story_adjustment") or {}).get(
        "story_snapshot"
    )
    if not baseline:
        raise ConflictError("旧方案缺少故事基线，请重新生成方案")
    # Claim the parent in the same transaction as creating the continuation job.
    claimed = await session.execute(
        update(Job)
        .where(
            Job.id == parent.id,
            Job.result == parent.result,
        )
        .values(
            result={**dict(parent.result or {}), "action_preview": {**preview, "status": "syncing"}}
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise ConflictError("方案正在处理，请刷新查看任务进度")
    scope = {"section": "overview"}
    sync_parameters = {
        "agent_workspace": "outline",
        "story_adjustment": scope,
        "source_artifacts": parameters.get("source_artifacts")
        or {"story_bible": preview["source"]},
        "story_snapshot": baseline,
        "adjustment_request": parameters.get("adjustment_request", ""),
        "story_sync": {
            "parent_job_id": parent.id,
            "selected_option_index": selected_index,
            "choice": choice,
        },
    }
    original_request = parameters.get("adjustment_request", "")
    prompt = f"""用户已选择故事方案，立即联动生成完整故事设定，不再讨论或确认。只输出JSON：reply、story_bible、episode_outline(null)。
story_bible包含title、logline、genre、tone、audience、world、themes、characters、event_timeline。
保持所选方案的故事方向；如按用户要求更改角色姓名，必须同步重写梗概、世界观及事件中的姓名，不要照抄方案中的旧称。角色的目标、冲突、成长及人物资料与事件因果必须匹配新世界观、基调和主题。
尽量保留符合新方向的角色和事件，不为改动而改动；保留已有角色character_id，改名保留原名aliases，不将同一身份分配给多个人。
角色必须包含name、role、goal、conflict、arc，并保留/合理调整年龄age、description、personality、appearance、costume、voice、importance、narrative_function、appearance_scope等资料。age用字符串或null，其他描述字段用字符串，aliases用字符串数组。
事件必须包含title、summary、episode_hint(整数或null)，不能只有建议；角色及事件不得为空。
用户原要求：{original_request}
结构与剧集规格：{story_bible_strategy_prompt(item.settings)}
所选故事概览：{json.dumps(choice, ensure_ascii=False)}
原故事基线：{json.dumps(baseline, ensure_ascii=False)}
完成前自检人物动机、事件因果、伏笔与主题的一致性，不生成分集大纲，不修改资产。"""
    child = await create_creation_job(
        session,
        item,
        JOB_TARGET_OUTLINE_AGENT,
        prompt,
        "故事正在联动生成，请等待当前任务完成",
        parameters=sync_parameters,
        provider_model_id=parent.payload.get("provider_model_id"),
    )
    child.payload = {
        **child.payload,
        "agent_execution": {
            **dict(parent.payload.get("agent_execution") or {}),
            "surface": "story_sync",
        },
    }
    child.max_attempts = 1
    from app.services.long_form_workflow import BATCH, configure
    if int(item.settings.get("episode_count") or 10) > BATCH:
        await configure(session, item, child, "story_sync")
    preview.update(
        status="syncing", continuation_job_id=child.id, selected_option_index=selected_index
    )
    parent.result = {**dict(parent.result or {}), "action_preview": preview}
    message = await append_message(
        session,
        item.id,
        "user",
        "agent_request",
        f"采用方案 {selected_index + 1}，同步概览、角色与事件",
        parameters=sync_parameters,
    )
    message.job_id = child.id
    await session.flush()


def validate_synced_story(parameters, proposed, settings):
    from app.services.story_bible_structure_review import validate_story_bible_structure

    content = StoryBibleContent.model_validate(proposed).model_dump()
    if not content["characters"] or not content["event_timeline"]:
        raise ConflictError("联动结果缺少角色或事件，原故事未修改")
    names = [row["name"].strip() for row in content["characters"]]
    identities = [
        row.get("character_id") for row in content["characters"] if row.get("character_id")
    ]
    if not all(names) or len(set(names)) != len(names) or len(set(identities)) != len(identities):
        raise ConflictError("联动结果角色身份重复或为空，原故事未修改")
    if any(
        not row["title"].strip() or not row["summary"].strip() for row in content["event_timeline"]
    ):
        raise ConflictError("联动结果存在空事件，原故事未修改")
    content = preserve_story_fields(parameters["story_snapshot"], content)
    previous_names = {
        row["character_id"]: row["name"]
        for row in parameters["story_snapshot"].get("characters", [])
        if row.get("character_id")
    }
    baseline_names = {
        row["name"] for row in parameters["story_snapshot"].get("characters", [])
    }
    passages = [
        (field, " ".join(content[field]) if isinstance(content[field], list) else str(content[field]))
        for field in OVERVIEW_FIELDS
    ]
    passages += [
        (f"事件 {index + 1}", f'{event["title"]} {event["summary"]}')
        for index, event in enumerate(content["event_timeline"])
    ]
    for person in content["characters"]:
        name = person["name"].strip()
        old_names = {previous_names.get(person.get("character_id"))}
        old_names.update(alias for alias in person.get("aliases", []) if alias in baseline_names)
        for old_name in old_names - {None, name}:
            if any(old_name in passage and name not in passage for _, passage in passages):
                raise ConflictError(
                    f"联动结果仍使用角色旧名“{old_name}”，请修正为“{name}”后重新处理；原故事未修改"
                )
    validate_story_bible_structure(content, settings)
    return content


async def finalize_story_sync(session, item, job, result):
    from app.services.creation_agent_service import apply_outline_agent_action
    from app.services.creation_session_service import append_message

    parameters = job.payload["parameters"]
    await apply_outline_agent_action(
        session,
        item,
        job.id,
        result["action_preview"]["source"]["version"],
        item.owner_id,
        finalized_result=result,
    )
    result.update(job.result or {})
    parent = await session.get(Job, parameters["story_sync"]["parent_job_id"])
    if parent is not None:
        parent_preview = dict((parent.result or {}).get("action_preview") or {})
        parent_preview.update(
            status="applied",
            applied_artifact_id=result["action_preview"]["applied_artifact_id"],
            applied_version=result["action_preview"]["applied_version"],
        )
        parent.result = {**dict(parent.result or {}), "action_preview": parent_preview}
    await append_message(
        session,
        item.id,
        "assistant",
        "story_sync_completed",
        "故事概览、角色与事件已整体保存为新版本；已有大纲、正文与资产保留，请按新版本核对。",
        job_id=job.id,
    )
