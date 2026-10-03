"""项目创作会话路由：创意方向、大纲 Agent、剧本研读、资产拆解与 Artifact 恢复。

路由只负责参数校验、调用 service 与提交事务，不写查询逻辑。
路径相对 `/projects` 前缀，由 projects.router 统一 include。
"""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, ProjectDep, SessionDep
from app.core.errors import ConflictError
from app.schemas.creation import (
    AgentActionApplyRequest,
    ArtifactRestoreRequest,
    CreationArtifactOut,
    CreationSessionOut,
    CreativeDirectionProposeOut,
    CreativeDirectionProposeRequest,
    CreativeDirectionSubmit,
    CreativeSpecsUpdate,
    CreativeStoryConfirm,
    OutlineAgentRequest,
    ReferenceChunkOut,
    ScriptAssetCandidateMergeRequest,
    ScriptAssetCandidateUpdate,
    ScriptAssetBreakdownRequest,
)
from app.schemas.job import JobOut
from app.services import creation_service

router = APIRouter(tags=["projects"])


@router.get("/{project_id}/creation-session", response_model=CreationSessionOut)
async def get_project_creation_session(
    project: ProjectDep, session: SessionDep, compact: bool = False
) -> CreationSessionOut:
    item = await creation_service.get_or_create_project_session(session, project)
    if dict((item.settings or {}).get("asset_breakdown") or {}).get("status") == "awaiting_confirmation":
        try:
            await creation_service._asset_breakdown_for_review(session, item)
        except ConflictError:
            # Review normalization may mark an outdated source as stale. The
            # session still needs to load so the UI can show that resolution.
            pass
    await session.commit()
    return CreationSessionOut.model_validate(
        await creation_service.to_session_out(session, item, compact=compact)
    )


@router.post(
    "/{project_id}/creation-session/creative/proposals",
    response_model=CreativeDirectionProposeOut,
)
async def propose_project_creative_directions(
    payload: CreativeDirectionProposeRequest,
    project: ProjectDep,
    session: SessionDep,
) -> CreativeDirectionProposeOut:
    item = await creation_service.get_or_create_project_session(session, project)
    result = await creation_service.propose_creative_directions(
        item,
        genre=payload.genre,
        conflict=payload.conflict,
        characters=payload.characters,
        tone=payload.tone,
    )
    await session.commit()
    return CreativeDirectionProposeOut.model_validate(result)


@router.post(
    "/{project_id}/creation-session/creative/proposal-jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_creative_direction_job(
    payload: CreativeDirectionProposeRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_creative_direction_job(
        session, item, genre=payload.genre, conflict=payload.conflict,
        characters=payload.characters, tone=payload.tone,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.patch(
    "/{project_id}/creation-session/creative/specs",
    response_model=CreationSessionOut,
)
async def update_project_creative_specs(
    payload: CreativeSpecsUpdate,
    project: ProjectDep,
    session: SessionDep,
) -> CreationSessionOut:
    item = await creation_service.get_or_create_project_session(session, project)
    await creation_service.update_creative_specs(
        session,
        item,
        project,
        episode_count=payload.episode_count,
        episode_duration=payload.episode_duration,
        market=payload.market,
        narrative_spec=payload.narrative_spec.model_dump() if payload.narrative_spec else None,
        expected_narrative_revision=payload.expected_narrative_revision,
    )
    await session.commit()
    return CreationSessionOut.model_validate(
        await creation_service.to_session_out(session, item)
    )


@router.post(
    "/{project_id}/creation-session/creative/direction",
    response_model=CreationSessionOut,
)
async def submit_project_creative_direction(
    payload: CreativeDirectionSubmit,
    project: ProjectDep,
    session: SessionDep,
) -> CreationSessionOut:
    item = await creation_service.get_or_create_project_session(session, project)
    await creation_service.submit_creative_direction(
        session,
        item,
        payload.selected_option,
        payload.extra_requirements,
        payload.proposal.model_dump() if payload.proposal else None,
        payload.proposal_fingerprint,
    )
    await session.commit()
    return CreationSessionOut.model_validate(
        await creation_service.to_session_out(session, item)
    )


@router.post(
    "/{project_id}/creation-session/creative/confirm",
    response_model=JobOut | CreationSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_project_creative_story(
    payload: CreativeStoryConfirm,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut | CreationSessionOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.confirm_creative_story(
        session, item, payload.action, payload.adjustment
    )
    await session.commit()
    if job is not None:
        return JobOut.model_validate(job)
    return CreationSessionOut.model_validate(
        await creation_service.to_session_out(session, item)
    )


@router.get("/{project_id}/creation-session/chunks", response_model=list[ReferenceChunkOut])
async def list_project_reference_chunks(
    project: ProjectDep, session: SessionDep
) -> list[ReferenceChunkOut]:
    item = await creation_service.get_or_create_project_session(session, project)
    await session.commit()
    from app.services.source_index_service import resolve_source_text
    source_text = await resolve_source_text(session, item)
    return [
        ReferenceChunkOut.model_validate(chunk)
        for chunk in creation_service.list_reference_chunks(item, source_text)
    ]


@router.post(
    "/{project_id}/creation-session/agent",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_project_outline_agent(
    payload: OutlineAgentRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_outline_agent_job(
        session,
        item,
        payload.message,
        payload.attachment_chunk_ids,
        [attachment.model_dump() for attachment in payload.attachments],
        optimize_all=payload.optimize_all,
        character_batch_completion=(
            payload.character_batch_completion.model_dump()
            if payload.character_batch_completion else None
        ),
        character_outline_coverage=(
            payload.character_outline_coverage.model_dump()
            if payload.character_outline_coverage else None
        ),
        story_overview_adjustment=(
            payload.story_overview_adjustment.model_dump()
            if payload.story_overview_adjustment else None
        ),
        event_timeline_adjustment=(
            payload.event_timeline_adjustment.model_dump()
            if payload.event_timeline_adjustment else None
        ),
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/agent-actions/{job_id}/apply",
    response_model=CreationSessionOut,
)
async def apply_outline_agent_action(
    job_id: int,
    payload: AgentActionApplyRequest,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> CreationSessionOut:
    item = await creation_service.get_or_create_project_session(session, project)
    updated = await creation_service.apply_outline_agent_action(
        session, item, job_id, payload.expected_source_version, user.id,
        selected_character_keys=payload.selected_character_keys,
        selected_option_index=payload.selected_option_index,
    )
    await session.commit()
    return CreationSessionOut.model_validate(await creation_service.to_session_out(session, updated))


@router.post(
    "/{project_id}/agent-actions/{job_id}/reject",
    response_model=JobOut,
)
async def reject_agent_action(
    job_id: int,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await creation_service.reject_agent_action(session, job_id, user.id, project.id)
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/script-study",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_project_script_study(
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_uploaded_script_optimization_job(session, item)
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/optional-extractions/episode-outline",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def extract_project_episode_outline(
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_batched_script_study_job(
        session, item, finalized_only=True
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/optional-extractions/story-bible",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def extract_project_story_bible(
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_script_story_extraction_job(session, item)
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/asset-breakdown",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_project_asset_breakdown(
    project: ProjectDep,
    session: SessionDep,
    payload: ScriptAssetBreakdownRequest = ScriptAssetBreakdownRequest(),
) -> JobOut:
    item = await creation_service.get_or_create_project_session(session, project)
    job = await creation_service.create_script_asset_breakdown_job(
        session,
        item,
        requirement_types=payload.requirement_types,
        episode_numbers=payload.episode_numbers,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post(
    "/{project_id}/creation-session/asset-breakdown/recheck",
    response_model=dict[str, int],
)
async def recheck_project_asset_breakdown(project: ProjectDep, session: SessionDep) -> dict[str, int]:
    from app.services.creation_breakdown_review import _asset_breakdown_for_review

    item = await creation_service.get_or_create_project_session(session, project)
    _, breakdown = await _asset_breakdown_for_review(session, item)
    await session.commit()
    return {"blocking_issue_count": breakdown.get("blocking_issue_count", 0),
            "selected_count": breakdown.get("selected_count", 0)}


@router.post(
    "/{project_id}/creation-session/asset-breakdown/confirm",
    response_model=dict[str, int],
)
async def confirm_project_asset_breakdown(
    project: ProjectDep,
    session: SessionDep,
) -> dict[str, int]:
    item = await creation_service.get_or_create_project_session(session, project)
    counts = await creation_service.confirm_script_asset_breakdown(session, item)
    await session.commit()
    return counts


@router.post(
    "/{project_id}/creation-session/asset-breakdown/fill-audio-prompts",
    response_model=dict[str, int],
)
async def fill_project_audio_candidate_prompts(
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict[str, int]:
    item = await creation_service.get_or_create_project_session(session, project)
    result = await creation_service.fill_missing_audio_candidate_prompts(
        session, item, user.id
    )
    await session.commit()
    return result


@router.post(
    "/{project_id}/creation-session/asset-breakdown/acknowledge-formal-sources",
    response_model=dict[str, int],
)
async def acknowledge_project_asset_formal_sources(
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict[str, int]:
    item = await creation_service.get_or_create_project_session(session, project)
    result = await creation_service.acknowledge_formal_script_candidate_reviews(
        session, item, user.id
    )
    await session.commit()
    return result


@router.patch(
    "/{project_id}/creation-session/asset-breakdown/candidates/{candidate_id}",
    response_model=dict[str, object],
)
async def update_project_asset_candidate(
    candidate_id: int,
    payload: ScriptAssetCandidateUpdate,
    project: ProjectDep,
    session: SessionDep,
) -> dict[str, object]:
    item = await creation_service.get_or_create_project_session(session, project)
    candidate = await creation_service.update_script_asset_candidate(
        session, item, candidate_id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return candidate


@router.post(
    "/{project_id}/creation-session/asset-breakdown/candidates/{candidate_id}/merge",
    response_model=dict[str, object],
)
async def merge_project_asset_candidate(
    candidate_id: int,
    payload: ScriptAssetCandidateMergeRequest,
    project: ProjectDep,
    session: SessionDep,
) -> dict[str, object]:
    item = await creation_service.get_or_create_project_session(session, project)
    candidate = await creation_service.merge_script_asset_candidate(
        session, item, candidate_id, payload.target_candidate_id
    )
    await session.commit()
    return candidate


@router.post(
    "/{project_id}/creation-session/asset-breakdown/reject",
    response_model=dict[str, object],
)
async def reject_project_asset_breakdown(
    project: ProjectDep,
    session: SessionDep,
) -> dict[str, object]:
    item = await creation_service.get_or_create_project_session(session, project)
    breakdown = await creation_service.reject_script_asset_breakdown(session, item)
    await session.commit()
    return breakdown


@router.post(
    "/{project_id}/creation-session/artifacts/{artifact_id}/restore",
    response_model=CreationArtifactOut,
)
async def restore_project_artifact(
    artifact_id: int,
    payload: ArtifactRestoreRequest,
    project: ProjectDep,
    session: SessionDep,
) -> CreationArtifactOut:
    item = await creation_service.get_or_create_project_session(session, project)
    artifact = await creation_service.restore_artifact_version(
        session, item, artifact_id, payload.artifact_type, payload.expected_current_id, payload.expected_revision
    )
    await session.commit()
    return CreationArtifactOut.model_validate(artifact)
