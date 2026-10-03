"""Project-owned edit identity, separate from production plans and T1 drafts."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class EditProject(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "edit_projects"
    __table_args__ = (
        UniqueConstraint("project_id", "creation_request_id", name="uq_edit_projects_creation"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    creation_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    creation_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="SET NULL")
    )
    frame_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )


class EditProjectReceipt(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "edit_project_receipts"
    __table_args__ = (
        UniqueConstraint("edit_project_id", "request_id", name="uq_edit_project_receipts_request"),
    )

    edit_project_id: Mapped[int] = mapped_column(
        ForeignKey("edit_projects.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
