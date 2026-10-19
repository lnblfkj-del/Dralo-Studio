"""Immutable execution settings and separately rotatable video account credentials."""

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, event, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin
from app.models.workspace_scoped import WorkspaceScoped


class VideoAccount(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "video_accounts"
    __table_args__ = (Index("uq_video_account_current", "provider_id", unique=True,
                           sqlite_where=text('"current" = true'), postgresql_where=text('"current" = true')),)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="RESTRICT"), index=True)
    current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)


class VideoExecutionVersion(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "video_execution_versions"
    __table_args__ = (UniqueConstraint("model_id", "fingerprint", name="uq_video_execution_version"),)
    model_id: Mapped[int] = mapped_column(ForeignKey("provider_models.id", ondelete="RESTRICT"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("video_accounts.id", ondelete="RESTRICT"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class VideoAdapterRelease(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "video_adapter_releases"
    __table_args__ = (UniqueConstraint("workspace_id", "adapter_key", "version", name="uq_video_adapter_release"),
        Index("uq_video_adapter_local_release", "adapter_key", "version", unique=True,
              sqlite_where=text("workspace_id IS NULL"), postgresql_where=text("workspace_id IS NULL")))
    adapter_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    package: Mapped[dict] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


@event.listens_for(VideoExecutionVersion, "before_update")
def immutable_version(_mapper, _connection, _target):
    raise ValueError("Video execution versions are immutable")


@event.listens_for(VideoAdapterRelease, "before_update")
def immutable_adapter(_mapper, _connection, target):
    from sqlalchemy import inspect
    if any(inspect(target).attrs[key].history.has_changes() for key in (
            "adapter_key", "version", "fingerprint", "package", "workspace_id")):
        raise ValueError("Installed adapter versions are immutable; install a new release")
