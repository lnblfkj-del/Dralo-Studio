"""Immutable episode manuscript snapshots; deleted only with their parent episode."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, utcnow


class ScriptVersion(IdMixin, WorkspaceScoped, Base):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("episode_id", "revision", name="uq_script_version_revision"),)

    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    script: Mapped[str] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    note: Mapped[str | None] = mapped_column(String(120))
    restored_from: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
