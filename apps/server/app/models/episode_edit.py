"""Isolated episode edit drafts; not part of the production plan."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class EpisodeEditDraft(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "episode_edit_drafts"
    __table_args__ = (UniqueConstraint("episode_id", name="uq_episode_edit_drafts_episode"),)

    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class EpisodeEditReceipt(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "episode_edit_receipts"
    __table_args__ = (UniqueConstraint("episode_id", "request_id", name="uq_episode_edit_receipts_request"),)

    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
