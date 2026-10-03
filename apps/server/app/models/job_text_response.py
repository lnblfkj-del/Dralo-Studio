"""Short-lived paid text responses preserved for local recovery."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class JobTextResponse(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "job_text_responses"
    __table_args__ = (
        Index("ix_job_text_responses_job_received", "job_id", "received_at"),
        Index("ix_job_text_responses_expires", "expires_at"),
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[int | None] = mapped_column(
        ForeignKey("billing_calls.id", ondelete="SET NULL"), unique=True, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    streaming: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="available")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
