"""Account-private, versioned object storage; no installation credentials."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class UserObjectStorage(Base, TimestampMixin):
    __tablename__ = "user_object_storage"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    sync: Mapped[dict] = mapped_column(JSON, default=dict)


class ObjectStorageVersion(Base, TimestampMixin):
    __tablename__ = "object_storage_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
