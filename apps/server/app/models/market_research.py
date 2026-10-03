"""短剧市场探查的持久化运行记录。"""

from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class MarketResearchRun(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "market_research_runs"
    __table_args__ = (Index("ix_market_research_owner_created", "owner_id", "created_at"),)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    market: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    platforms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    genres: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    audience: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    time_range: Mapped[str] = mapped_column(String(16), nullable=False, default="30d")
    keywords: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    selected_idea_index: Mapped[int | None] = mapped_column()
    adopted_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )


class MarketIdeaProject(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """A project adopted from one specific idea in a market-research run."""

    __tablename__ = "market_idea_projects"
    __table_args__ = (
        UniqueConstraint(
            "market_research_run_id",
            "idea_index",
            name="uq_market_idea_projects_run_idea",
        ),
        UniqueConstraint("project_id", name="uq_market_idea_projects_project"),
    )

    market_research_run_id: Mapped[int] = mapped_column(
        ForeignKey("market_research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idea_index: Mapped[int] = mapped_column(nullable=False)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
