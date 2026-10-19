"""Workspace-owned, immutable reusable video configuration releases."""

from sqlalchemy import JSON, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin
from app.models.workspace_scoped import WorkspaceScoped, local_catalog_index


class VideoConfigurationPreset(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "video_configuration_presets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "release_key", name="uq_video_preset_release"),
        local_catalog_index("video_configuration_presets", "release_key"),
    )
    release_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    model_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    package: Mapped[dict] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(VideoConfigurationPreset, "before_update")
def immutable_preset(_mapper, _connection, _target):
    raise ValueError("Reusable video releases are immutable; save a new version")
