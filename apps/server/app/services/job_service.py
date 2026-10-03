"""SQLite Job 状态机与原子领取（兼容层）。

本模块作为兼容入口，重导出拆分后的各 Service 公共接口，保持现有调用方无需修改。
后续新代码请直接从对应子模块导入。
"""

# 常量
from app.core.config import settings
from app.models import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DOWNLOADING,
    JOB_STATUS_FAILED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RETRYING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_EXPORT,
    JOB_TYPE_IMAGE,
    JOB_TYPE_TEXT,
    JOB_TYPE_VIDEO,
)

TERMINAL_STATUSES = {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
BATCH_PARENT_TARGETS = {
    "video_batch",
    "episode_video_batch",
    "script_study_batch_group",
    "script_asset_breakdown_group",
    "episode_script_batch",
    "asset_image_batch",
    "episode_director_pipeline",
}
LOCAL_JOB_TYPES = {"media_process", "source_parse", JOB_TYPE_EXPORT}
ACTIVE_LEASE_STATUSES = {
    JOB_STATUS_RUNNING,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_DOWNLOADING,
}
REMOTE_EXECUTION_PHASES = {"poll", "download"}

# 任务创建
# 模型并发控制
from app.services.job_concurrency_service import (
    _aware_utc,
    _job_provider_model_id,
    record_model_rate_limit,
    record_model_success,
)
from app.services.job_creation_service import (
    create_episode_export_job,
    create_image_job,
    create_text_job,
    create_video_job,
    list_episode_export_versions,
    preflight_episode_export,
)

# 任务管理
from app.services.job_manage_service import (
    bulk_manage_jobs,
    delete_job,
    pause_batch,
    purge_job,
    restore_job,
    resume_batch,
)

# 视频定价
from app.services.job_pricing_service import (
    _aggregate_video_pricing,
    _video_model_pair,
)

# 任务查询
from app.services.job_query_service import (
    _job_list_filters,
    get_job,
    job_diagnostic,
    list_child_jobs,
    list_jobs,
    matching_job_ids,
    retry_block_reason,
)

# 任务状态机
from app.services.job_state_service import (
    aggregate_parent_job,
    cancel_job,
    claim_next_job,
    defer_remote_job,
    mark_downloading,
    mark_failed,
    mark_processing,
    mark_succeeded,
    recover_expired_leases,
    release_due_retries,
    renew_lease,
    retry_job,
)
from app.services.job_video_batch_service import (
    build_episode_video_plan,
    build_segment_video_plan,
    create_batch_video_jobs,
    create_episode_segment_jobs,
    create_segment_video_job,
    create_shot_video_job,
    start_episode_video_batch,
    start_project_episode_batches,
    start_segment_video_attempt,
)

__all__ = [
    # 常量
    "TERMINAL_STATUSES",
    "BATCH_PARENT_TARGETS",
    "LOCAL_JOB_TYPES",
    "ACTIVE_LEASE_STATUSES",
    "REMOTE_EXECUTION_PHASES",
    # 任务创建
    "build_episode_video_plan",
    "build_segment_video_plan",
    "create_batch_video_jobs",
    "create_episode_export_job",
    "create_episode_segment_jobs",
    "create_image_job",
    "create_segment_video_job",
    "create_shot_video_job",
    "create_text_job",
    "create_video_job",
    "list_episode_export_versions",
    "preflight_episode_export",
    "start_episode_video_batch",
    "start_segment_video_attempt",
    "start_project_episode_batches",
    # 任务查询
    "_job_list_filters",
    "get_job",
    "job_diagnostic",
    "list_child_jobs",
    "list_jobs",
    "matching_job_ids",
    "retry_block_reason",
    # 任务状态机
    "aggregate_parent_job",
    "cancel_job",
    "claim_next_job",
    "defer_remote_job",
    "mark_downloading",
    "mark_failed",
    "mark_processing",
    "mark_succeeded",
    "recover_expired_leases",
    "release_due_retries",
    "renew_lease",
    "retry_job",
    # 任务管理
    "bulk_manage_jobs",
    "delete_job",
    "pause_batch",
    "purge_job",
    "restore_job",
    "resume_batch",
    # 模型并发控制
    "_aware_utc",
    "_job_provider_model_id",
    "record_model_rate_limit",
    "record_model_success",
    # 视频定价
    "_aggregate_video_pricing",
    "_video_model_pair",
]
