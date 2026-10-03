"""M4 无限画布布局数据，不承载项目唯一业务事实。"""

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class CanvasDocument(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "canvas_documents"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    viewport: Mapped[dict[str, float]] = mapped_column(
        JSON, nullable=False, default=lambda: {"x": 0, "y": 0, "zoom": 1}
    )


class CanvasNode(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "canvas_nodes"
    __table_args__ = (
        UniqueConstraint("canvas_id", "node_key", name="uq_canvas_nodes_key"),
    )

    canvas_id: Mapped[int] = mapped_column(
        ForeignKey("canvas_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    z_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_key: Mapped[str | None] = mapped_column(String(64), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CanvasEdge(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "canvas_edges"
    __table_args__ = (
        UniqueConstraint("canvas_id", "edge_key", name="uq_canvas_edges_key"),
    )

    canvas_id: Mapped[int] = mapped_column(
        ForeignKey("canvas_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_handle: Mapped[str | None] = mapped_column(String(64))
    target_handle: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
