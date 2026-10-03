"""Pre-project script import sessions used by the upload review flow."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin

IMPORT_STATUS_DRAFT = "draft"
IMPORT_STATUS_CONFIRMING = "confirming"
IMPORT_STATUS_CONFIRMED = "confirmed"

MATERIAL_TYPE_UNKNOWN = "unknown"
MATERIAL_TYPE_STORY_OUTLINE = "story_outline"
MATERIAL_TYPE_FULL_SCRIPT = "full_script"


class ScriptImportSession(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """An immutable source plus editable parsing decisions before project creation."""

    __tablename__ = "script_import_sessions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "confirmation_request_id",
            name="uq_script_import_confirmation_request",
        ),
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), unique=True, index=True
    )
    creation_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("creation_sessions.id", ondelete="SET NULL"), unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    material_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    episode_boundaries: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    corrections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=IMPORT_STATUS_DRAFT, index=True
    )
    confirmation_request_id: Mapped[str | None] = mapped_column(String(100))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
