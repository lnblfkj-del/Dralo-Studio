"""Fresh planning storage; no foreign keys to legacy plans or segment tasks."""

from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin
from app.models.workspace_scoped import WorkspaceScoped


class EpisodePlanningRecord(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "episode_planning_records"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "execution_epoch", "revision", name="uq_episode_planning_revision"
        ),
        CheckConstraint("revision > 0", name="ck_episode_planning_revision"),
        CheckConstraint("length(fingerprint) = 64", name="ck_episode_planning_fingerprint"),
    )

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    execution_epoch: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int]
    fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    analysis_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class PlanningResponseRecord(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "planning_response_records"
    __table_args__ = (
        UniqueConstraint("plan_id", "request_id", name="uq_planning_response_request"),
        CheckConstraint("status IN ('available', 'saved')", name="ck_planning_response_status"),
        CheckConstraint(
            "length(response_fingerprint) = 64", name="ck_planning_response_fingerprint"
        ),
        CheckConstraint(
            "status != 'saved' OR detail IS NOT NULL", name="ck_planning_response_saved"
        ),
    )

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("episode_planning_records.id", ondelete="CASCADE"),
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(128))
    segment_key: Mapped[str] = mapped_column(String(128))
    response_fingerprint: Mapped[str] = mapped_column(String(64))
    raw_response: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="available")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
