"""M3 创作会话与 Story Bible 业务逻辑（兼容层）。

本模块作为兼容入口，重导出拆分后的各 Service 公共接口，保持现有调用方无需修改。
后续新代码请直接从对应子模块导入。
"""

# 兼容导出：会话基础
from app.services.creation_session_service import (
    CREATION_ARTIFACT_TYPES,
    JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL,
    JOB_TARGET_CREATIVE_DIRECTION,
    JOB_TARGET_EPISODE_SCRIPT_GENERATION,
    JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION,
    JOB_TARGET_OUTLINE_AGENT,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH,
    JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP,
    JOB_TARGET_SCRIPT_IMPORT,
    JOB_TARGET_SCRIPT_STUDY_BATCH,
    JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP,
    JOB_TARGET_SCRIPT_STORY_EXTRACTION,
    REFERENCE_CHUNK_CHARS,
    SCRIPT_STUDY_BATCH_SIZE,
    append_message,
    chunk_reference_text,
    create_artifact,
    create_session,
    get_artifact,
    get_or_create_project_session,
    get_session,
    list_reference_chunks,
    parse_structured_result,
    to_session_out,
)

# 兼容导出：Agent 执行
from app.services.creation_agent_service import (
    _attach_agent_execution,
    _resolve_agent_execution,
    apply_outline_agent_action,
    create_creation_job,
    create_outline_agent_job,
    reject_agent_action,
)

# 兼容导出：Story Bible
from app.services.creation_story_service import (
    build_story_bible_prompt,
    confirm_story_bible,
    create_story_bible_job,
    parse_story_bible,
    update_story_bible,
)

# 兼容导出：大纲与剧本
from app.services.creation_outline_service import (
    apply_episode_scene_shot_proposal,
    apply_episode_script_optimization,
    confirm_episode_outline,
    create_episode_outline_job,
    create_episode_scene_shot_proposal_job,
    create_episode_script_generation_jobs,
    create_episode_script_job,
    create_episode_script_optimization_job,
    create_scene_shot_draft_job,
    get_first_episode,
    publish_first_episode,
    publish_scene_shot_draft,
    restore_artifact_version,
    save_episode_outline_version,
    update_creation_artifact,
)

# 兼容导出：剧本研读与资产拆解
from app.services.creation_breakdown_service import (
    _asset_breakdown_for_review,
    _asset_breakdown_prompt,
    _refresh_asset_candidate_counts,
    acknowledge_formal_script_candidate_reviews,
    confirm_script_asset_breakdown,
    create_batched_script_study_job,
    create_script_asset_breakdown_job,
    create_uploaded_script_optimization_job,
    fill_missing_audio_candidate_prompts,
    merge_script_asset_candidate,
    reject_script_asset_breakdown,
    update_script_asset_candidate,
)

from app.services.creation_extraction_service import create_script_story_extraction_job
from app.services.creation_recovery_service import recover_legacy_creation_session
from app.services.script_continuity_review_service import (
    accept_issue as accept_script_continuity_issue,
    accept_manual_review as accept_script_continuity_manual_review,
    apply_repair as apply_script_continuity_repair,
    create_check_job as create_script_continuity_check_job,
    create_repair_job as create_script_continuity_repair_job,
    get_review as get_script_continuity_review,
)

# 兼容导出：创意方向
from app.services.creation_creative_service import (
    CREATIVE_DIRECTION_OPTIONS,
    _resolve_creative_direction,
    confirm_creative_story,
    create_creative_direction_job,
    propose_creative_directions,
    submit_creative_direction,
    update_creative_specs,
)

# 兼容导出：产物落库
from app.services.creation_finalize_service import (
    finalize_creation_job,
    mark_creation_job_failed,
)

__all__ = [
    # 会话基础
    "CREATION_ARTIFACT_TYPES",
    "JOB_TARGET_EPISODE_SCENE_SHOT_PROPOSAL",
    "JOB_TARGET_CREATIVE_DIRECTION",
    "JOB_TARGET_EPISODE_SCRIPT_GENERATION",
    "JOB_TARGET_EPISODE_SCRIPT_OPTIMIZATION",
    "JOB_TARGET_OUTLINE_AGENT",
    "JOB_TARGET_SCRIPT_ASSET_BREAKDOWN",
    "JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_BATCH",
    "JOB_TARGET_SCRIPT_ASSET_BREAKDOWN_GROUP",
    "JOB_TARGET_SCRIPT_IMPORT",
    "JOB_TARGET_SCRIPT_STUDY_BATCH",
    "JOB_TARGET_SCRIPT_STUDY_BATCH_GROUP",
    "JOB_TARGET_SCRIPT_STORY_EXTRACTION",
    "REFERENCE_CHUNK_CHARS",
    "SCRIPT_STUDY_BATCH_SIZE",
    "append_message",
    "chunk_reference_text",
    "create_artifact",
    "create_session",
    "get_artifact",
    "get_or_create_project_session",
    "get_session",
    "list_reference_chunks",
    "parse_structured_result",
    "to_session_out",
    # Agent 执行
    "_attach_agent_execution",
    "_resolve_agent_execution",
    "apply_outline_agent_action",
    "create_creation_job",
    "create_outline_agent_job",
    "reject_agent_action",
    # Story Bible
    "build_story_bible_prompt",
    "confirm_story_bible",
    "create_story_bible_job",
    "parse_story_bible",
    "update_story_bible",
    # 大纲与剧本
    "apply_episode_scene_shot_proposal",
    "apply_episode_script_optimization",
    "confirm_episode_outline",
    "create_episode_outline_job",
    "create_episode_scene_shot_proposal_job",
    "create_episode_script_generation_jobs",
    "create_episode_script_job",
    "create_episode_script_optimization_job",
    "create_scene_shot_draft_job",
    "get_first_episode",
    "publish_first_episode",
    "publish_scene_shot_draft",
    "restore_artifact_version",
    "save_episode_outline_version",
    "update_creation_artifact",
    # 剧本研读与资产拆解
    "_asset_breakdown_for_review",
    "_asset_breakdown_prompt",
    "_refresh_asset_candidate_counts",
    "acknowledge_formal_script_candidate_reviews",
    "confirm_script_asset_breakdown",
    "create_batched_script_study_job",
    "create_script_asset_breakdown_job",
    "create_uploaded_script_optimization_job",
    "fill_missing_audio_candidate_prompts",
    "create_script_story_extraction_job",
    "create_script_continuity_check_job",
    "create_script_continuity_repair_job",
    "get_script_continuity_review",
    "apply_script_continuity_repair",
    "accept_script_continuity_issue",
    "accept_script_continuity_manual_review",
    "merge_script_asset_candidate",
    "reject_script_asset_breakdown",
    "recover_legacy_creation_session",
    "update_script_asset_candidate",
    # 创意方向
    "CREATIVE_DIRECTION_OPTIONS",
    "_resolve_creative_direction",
    "confirm_creative_story",
    "create_creative_direction_job",
    "propose_creative_directions",
    "submit_creative_direction",
    "update_creative_specs",
    # 产物落库
    "finalize_creation_job",
    "mark_creation_job_failed",
]
