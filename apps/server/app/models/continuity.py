"""Versioned, evidence-backed story continuity records."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin

CONTINUITY_CONFIRMATION_DRAFT = "draft"
CONTINUITY_CONFIRMATION_CONFIRMED = "confirmed"
CONTINUITY_STATUS_ACTIVE = "active"
CONTINUITY_STATUS_SUPERSEDED = "superseded"


class StoryContinuityFact(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """A story fact tied to an exact source and revision.

    Evidence is intentionally retained when a newer script revision supersedes a
    record. This makes continuity decisions explainable and reversible.
    """

    __tablename__ = "story_continuity_facts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_ref",
            "source_revision",
            "fact_type",
            "fact_key",
            name="uq_story_continuity_source_fact",
        ),
        Index("ix_story_continuity_project_status", "project_id", "status"),
        Index("ix_story_continuity_project_type", "project_id", "fact_type"),
        Index("ix_story_continuity_episode", "source_episode_id"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True
    )
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CONTINUITY_STATUS_ACTIVE
    )
