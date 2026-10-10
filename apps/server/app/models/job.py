"""任务队列模型。

不引 Redis / Celery，队列直接建在 SQLite 上。并发要点见 DEVELOPMENT.md 7.2 / 7.3：
- Worker 用带条件的 UPDATE 原子领取任务，避免同一任务被多个 Worker 拿到。
- 领取时写入 worker_id 与 lease_expires_at；进程崩溃后由回收逻辑把过期租约的
  任务重置回 queued，而不是永久卡在 running。
表结构在 M1 就建好，M2 接入实际执行逻辑。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.core.video_submission import remote_video_task_id
from app.models.base import IdMixin, TimestampMixin

# 任务状态
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_PROCESSING = "processing"
JOB_STATUS_DOWNLOADING = "downloading"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_RETRYING = "retrying"

# 任务类型
JOB_TYPE_SCRIPT = "script"
JOB_TYPE_STORYBOARD = "storyboard"
JOB_TYPE_IMAGE = "image"
JOB_TYPE_VIDEO = "video"
JOB_TYPE_TTS = "tts"
JOB_TYPE_EXPORT = "export"
JOB_TYPE_TEXT = "text"


class Job(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # Worker 领取任务的主查询路径：status + priority + id
        Index("ix_jobs_status_priority", "status", "priority", "id"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        Index(
            "ix_jobs_active_provider", "provider_id", "status", "lease_expires_at"
        ),
        Index("ix_jobs_active_type", "job_type", "status", "lease_expires_at"),
        Index(
            "ix_jobs_remote_schedule",
            "status",
            "execution_phase",
            "available_at",
            "priority",
            "id",
        ),
        Index("ix_jobs_owner_deleted_created", "owner_id", "deleted_at", "created_at"),
        # 按业务对象类型 + 创建时间排序查询（任务中心按 target 分组/翻页）
        Index("ix_jobs_target_created", "target_type", "created_at"),
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), index=True
    )

    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOB_STATUS_QUEUED, index=True
    )
    # 数值越小越先执行
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    # 关联的业务对象，例如 shot / episode
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(Integer, index=True)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    execution_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: __import__(
            "app.services.execution_policy_service", fromlist=["current_snapshot"]
        ).current_snapshot(),
    )

    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 失败信息：code 供前端分支，message 面向用户
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # 租约：崩溃恢复依赖这两个字段
    worker_id: Mapped[str | None] = mapped_column(String(64), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # submit 使用本地槽位发起请求；poll 表示远端生成中、等待短时轮询；
    # download 表示远端已完成并进入结果落盘。旧任务统一视为 submit。
    execution_phase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="submit", index=True
    )
    # 删除任务只进入回收站；媒体、资产和账单均独立保留。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # 仅批次父任务使用。暂停只阻止尚未领取的子任务继续提交。
    batch_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 成本估算，单位为分。仅本地估算，不承诺与供应商账单一致。见 DEVELOPMENT.md 5.3
    cost_estimate: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))

    @property
    def pricing_estimate(self) -> dict[str, Any] | None:
        return (self.payload or {}).get("pricing_snapshot")

    @property
    def audio_policy_summary(self) -> dict[str, Any] | None:
        payload = self.payload or {}
        value = (payload.get("director_execution") or {}).get("audio_policy") or (payload.get("parameters") or {}).get("audio_policy")
        if not isinstance(value, dict):
            return None
        return {key: value.get(key) for key in ("version", "background_music", "voice_choice", "source")}

    @property
    def resolution(self) -> dict[str, Any] | None:
        value = (self.payload or {}).get("resolution")
        return value if isinstance(value, dict) else None

    @property
    def asset_response_truncated(self) -> bool:
        history = (self.payload or {}).get("attempt_history") or []
        return (
            self.target_type == "script_asset_breakdown_batch"
            and bool(history)
            and history[-1].get("error_code") == "MODEL_OUTPUT_TRUNCATED"
            and bool((self.payload or {}).get("text_submission", {}).get("response_received"))
        )

    @property
    def failure_detail(self) -> dict[str, Any] | None:
        from app.core.job_failure import failure_detail
        return failure_detail(self)

    @property
    def retry_block_reason(self) -> str | None:
        from app.core.retired_workflows import RETIRED_DIRECTOR_TARGETS, RETIRED_MESSAGE
        if self.target_type in RETIRED_DIRECTOR_TARGETS:
            return RETIRED_MESSAGE
        if self.target_type in {"episode_content_planning", "episode_content_analysis", "episode_content_detail"}:
            return "请从规划任务恢复未完成范围；新增调用需要先确认费用"
        if self.status not in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            return "仅失败或已取消的任务可以重试"
        resolution = self.resolution or {}
        if resolution.get("status") == "superseded":
            replacement = resolution.get("by_job_id")
            return f"已由成功任务 #{replacement} 替代，无需重试" if replacement else "已由后续成功任务替代，无需重试"
        if resolution.get("status") == "replacement_pending":
            return "已创建替代范围任务，请等待新任务完成"
        if self.job_type == "text" and (self.failure_detail or {}).get("action") == "none":
            return (self.failure_detail or {})["hint"]
        recovery_summary = dict((self.payload or {}).get("recovery_summary") or {})
        if self.target_type == "script_asset_breakdown_group":
            if int(
                recovery_summary.get("local_reprocess_pending")
                or recovery_summary.get("local_reprocess_required")
                or 0
            ) > 0:
                return "失败范围包含已保存模型响应，请先本地处理；仍失败后再确认重新调用"
            if int(recovery_summary.get("channel_check_required") or 0) > 0:
                return "失败范围的远端结果待核对，确认渠道未生成前禁止重新调用"
            if int(recovery_summary.get("local_reprocess_exhausted") or 0) > 0:
                return "失败范围本地重新处理仍未通过，请确认后仅重新调用失败范围"
        text_submission = (self.payload or {}).get("text_submission", {})
        if text_submission.get("response_received"):
            if self.job_type == "text" and (self.failure_detail or {}).get("action") == "retry":
                return None
            if self.asset_response_truncated:
                return "模型输出已截断，本地处理不能补全；请确认后按更小范围重新生成"
            return "模型响应已保存，请先重新处理已保存响应，禁止重复调用模型"
        if text_submission.get("status") == "submitted" and self.job_type != "text":
            return "模型请求结果或费用待核实，确认渠道未生成前禁止重新调用"
        audio = (self.payload or {}).get("audio_submission", {})
        if self.job_type in {"tts", "audio"} and audio.get("started"):
            from app.services.audio_result_lifecycle import result_expired
            if result_expired(self):
                return "音频恢复结果已过期；请在原页面确认费用后创建新任务，不会自动重新生成"
            if self.error_code == "AUDIO_PROVIDER_FAILED":
                return "渠道已明确生成失败；请在原页面确认费用后创建新的生成任务"
            if audio.get("result_received") or self.error_code == "AUDIO_SAVE_FAILED" or audio.get("id"):
                return None
            return "音频提交结果不确定，需先核对渠道记录；不会自动重发"
        if self.job_type == "tts" and (self.payload or {}).get("media_submission", {}).get("started"):
            return "配音提交结果不确定，需先核对渠道记录"
        submission = (self.payload or {}).get("video_submission", {})
        if submission.get("started") and not remote_video_task_id(submission):
            return "视频提交结果不确定，需先核对渠道任务和账单"
        return None

    @property
    def retry_allowed(self) -> bool:
        return self.retry_block_reason is None

    @property
    def paid_recall_allowed(self) -> bool:
        """Whether an asset-breakdown failure may expose explicit paid recovery."""
        if self.status not in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            return False
        if (self.resolution or {}).get("status") in {
            "superseded",
            "replacement_pending",
        }:
            return False
        if self.target_type == "script_asset_breakdown_group":
            summary = dict((self.payload or {}).get("recovery_summary") or {})
            return bool(summary.get("paid_recall_allowed"))
        if self.target_type != "script_asset_breakdown_batch":
            return False
        submission = dict((self.payload or {}).get("text_submission") or {})
        if submission.get("response_received"):
            recovery = dict((self.payload or {}).get("response_recovery") or {})
            return self.asset_response_truncated or bool(recovery.get("last_reprocess_error_code"))
        return submission.get("status") == "submitted"

    @property
    def text_response_recovery(self) -> dict[str, Any] | None:
        value = (self.payload or {}).get("response_recovery")
        if not isinstance(value, dict):
            return None
        recovery = {
            key: value.get(key)
            for key in (
                "response_id",
                "call_id",
                "attempt",
                "received_at",
                "expires_at",
                "status",
                "processed_at",
                "model_called",
                "last_reprocess_error_code",
                "last_reprocess_error_message",
            )
            if value.get(key) is not None
        }
        recovery["regeneration_required"] = self.asset_response_truncated or (self.failure_detail or {}).get("action") in {"retry", "recall"}
        return recovery

    @property
    def agent_execution(self) -> dict[str, Any] | None:
        """Non-secret snapshot of the Agent configuration used at submission."""
        value = (self.payload or {}).get("agent_execution")
        return value if isinstance(value, dict) else None

    @property
    def continuation_context(self) -> dict[str, Any] | None:
        if self.target_type != "outline_continuation":
            return None
        parameters = (self.payload or {}).get("parameters", {})
        keys = ("proposal_id", "stage", "number", "mode", "completed_count", "target_count")
        return {key: parameters.get(key) for key in keys}

    @property
    def runtime_progress(self) -> dict[str, Any]:
        """Safe, user-visible live text progress; excludes prompts and credentials."""
        runtime = dict((self.payload or {}).get("runtime_progress") or {})
        if self.status == JOB_STATUS_SUCCEEDED:
            runtime["stage"] = "awaiting_review"
        elif self.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            runtime["stage"] = self.status
        runtime.setdefault("stage", "queued")
        metrics = dict((self.result or {}).get("_execution_metrics") or {})
        if metrics:
            runtime["metrics"] = metrics
        return runtime

    @property
    def business_executor(self) -> dict[str, Any] | None:
        """Return the non-secret immutable executor audit for task details."""
        value = (self.payload or {}).get("business_executor")
        if not isinstance(value, dict):
            return None
        return {
            "executor_key": value.get("executor_key"),
            "executor_revision": value.get("executor_revision"),
            "approval_policy": value.get("approval_policy"),
            "model": value.get("model"),
            "skills": [
                {key: item.get(key) for key in (
                    "skill_id", "key", "name", "selected_version", "current_version"
                )}
                for item in value.get("skills", [])
                if isinstance(item, dict)
            ],
            "tool_keys": value.get("tool_keys", []),
            "billing_behavior": value.get("billing_behavior"),
        }

    @property
    def production_context(self) -> dict[str, Any] | None:
        """Safe E5 labels for task-center display; excludes prompts and URLs."""
        payload = self.payload or {}
        if self.target_type == "video_segment":
            return {
                "unit": "video_segment",
                "episode_id": payload.get("episode_id"),
                "episode_number": payload.get("episode_number"),
                "plan_id": payload.get("plan_id"),
                "segment_order": payload.get("segment_order"),
                "shot_ids": payload.get("shot_ids", []),
                "generation_duration": payload.get("generation_duration"),
            }
        if self.target_type == "episode_video_batch" and payload.get("production_unit") == "video_segment":
            return {
                "unit": "episode",
                "episode_id": payload.get("episode_id"),
                "episode_number": payload.get("episode_number"),
                "plan_id": payload.get("plan_id"),
                "plan_version": payload.get("plan_version"),
                "segment_count": len(payload.get("segment_ids", [])),
                "generation_duration": payload.get("total_generation_duration"),
            }
        return None

    @property
    def video_compilation(self) -> dict[str, Any] | None:
        """Expose a safe, immutable P7 compilation summary for task details."""
        parameters = (self.payload or {}).get("parameters") or {}
        value = parameters.get("video_compilation")
        if not isinstance(value, dict):
            return None
        package = value.get("director_shot_package")
        safe_package = None
        if isinstance(package, dict):
            safe_package = {
                key: package.get(key)
                for key in (
                    "schema_version", "project_id", "director_node_key",
                    "director_revision", "director_state_fingerprint",
                    "package_fingerprint", "aspect_ratio", "fps",
                    "duration_frames", "duration_seconds", "first_frame_media_id",
                    "last_frame_media_id", "preview_video_media_id",
                    "reference_media",
                )
            }
        return {
            "submission_fingerprint": value.get("submission_fingerprint"),
            "ready": value.get("ready"),
            "actions": value.get("actions", []),
            "blockers": value.get("blockers", []),
            "director_shot_package": safe_package,
        }

    @property
    def media_processing(self) -> dict[str, Any] | None:
        """Safe immutable processing provenance for task-center display."""
        payload = self.payload or {}
        processing = payload.get("processing")
        if self.job_type != "media_process" or not isinstance(processing, dict):
            return None
        operation = processing.get("operation")
        if not isinstance(operation, dict):
            return None
        return {
            "operation": operation,
            "source_media_id": processing.get("source_media_id"),
            "source_hash": payload.get("source_hash"),
            "audio_hash": payload.get("audio_hash"),
            "source_duration": (payload.get("metadata") or {}).get("duration"),
            "director_context": payload.get("director_context"),
            "output_kind": payload.get("output_kind"),
        }

    @property
    def execution_info(self) -> dict[str, Any] | None:
        """Safe recovery metadata; never expose provider configuration or payload."""
        payload = self.payload or {}
        audio = payload.get("audio_submission", {})
        if self.job_type in {"tts", "audio"} and audio.get("started"):
            from app.services.audio_result_lifecycle import result_expired
            expired = result_expired(self)
            saved = audio.get("result_received") or self.error_code == "AUDIO_SAVE_FAILED"
            return {"recovery": "check_required" if expired else "save_only" if saved else "check_required" if self.error_code == "AUDIO_PROVIDER_FAILED" else "query_only" if audio.get("id") else "check_required",
                    "task_id": audio.get("id"), "cancel_scope": "local_only",
                    "result_expires_at": audio.get("receipt_expires_at"), "result_expired": expired,
                    "phase": self.execution_phase, "started_at": audio.get("started_at")}
        submission = payload.get("image_submission", {})
        attempted = submission.get("attempted")
        if not attempted:
            submission = payload.get("video_submission", {})
            attempted = submission.get("started")
        if not attempted:
            return None
        return {
            "recovery": "query_only",
            "task_id": remote_video_task_id(submission),
            "business_id": submission.get("business_id"),
            "cancel_scope": "local_only",
            "phase": self.execution_phase,
            "started_at": submission.get("started_at"),
            "timeout_seconds": submission.get("timeout_seconds"),
        }

    def __repr__(self) -> str:
        return f"<Job id={self.id} type={self.job_type} status={self.status}>"
