"""M6D project-scoped canvas Agent conversations."""

from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class CanvasAgentThread(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "canvas_agent_threads"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新对话")


class CanvasAgentMessage(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "canvas_agent_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_canvas_agent_messages_sequence"),
    )

    thread_id: Mapped[int] = mapped_column(
        ForeignKey("canvas_agent_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
