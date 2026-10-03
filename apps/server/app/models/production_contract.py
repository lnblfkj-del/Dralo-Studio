"""R1 immutable receipts and content-addressed script records, reusing existing identities."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin


class AssetProductionReceipt(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "asset_production_receipts"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_asset_production_request"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)


class SegmentScriptSnapshot(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "segment_script_snapshots"
    __table_args__ = (
        UniqueConstraint("segment_id", "fingerprint", name="uq_segment_script_fingerprint"),
    )
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("video_segments.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
