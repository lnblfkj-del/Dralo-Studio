"""M3 创作会话、消息与结构化产物。"""

from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin

SESSION_STATUS_DRAFT = "draft"
SESSION_STATUS_GENERATING = "generating"
SESSION_STATUS_REVIEWING = "reviewing"
SESSION_STATUS_CONFIRMED = "confirmed"
SESSION_STATUS_OUTLINE_GENERATING = "outline_generating"
SESSION_STATUS_OUTLINE_REVIEWING = "outline_reviewing"
SESSION_STATUS_OUTLINE_CONFIRMED = "outline_confirmed"
SESSION_STATUS_SCRIPT_GENERATING = "script_generating"
SESSION_STATUS_SCRIPT_REVIEWING = "script_reviewing"
SESSION_STATUS_COMPLETED = "completed"
SESSION_STATUS_BREAKDOWN_GENERATING = "breakdown_generating"
SESSION_STATUS_BREAKDOWN_REVIEWING = "breakdown_reviewing"
SESSION_STATUS_BREAKDOWN_COMPLETED = "breakdown_completed"

ARTIFACT_TYPE_STORY_BIBLE = "story_bible"
ARTIFACT_TYPE_EPISODE_OUTLINE = "episode_outline"
ARTIFACT_TYPE_EPISODE_SCRIPT = "episode_script"
ARTIFACT_TYPE_SCENE_SHOT_DRAFT = "scene_shot_draft"
JOB_TARGET_OUTLINE_AGENT = "outline_agent"
ARTIFACT_STATUS_DRAFT = "draft"
ARTIFACT_STATUS_CONFIRMED = "confirmed"
ARTIFACT_STATUS_SUPERSEDED = "superseded"


class CreationSession(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "creation_sessions"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SESSION_STATUS_DRAFT, index=True
    )


class CreationMessage(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "creation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_creation_messages_sequence"),
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("creation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class CreationArtifact(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "creation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "artifact_type", "version", name="uq_creation_artifacts_version"
        ),
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("creation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 乐观锁令牌：同一草稿的任何内容写入都 +1。
    # 与 version（快照版本号，保存新版本时 +1）语义不同，不可混用。
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
