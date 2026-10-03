"""Durable local cleanup, independent of user deletion and external buckets."""

from sqlalchemy import JSON, BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin


class MediaCleanup(IdMixin, TimestampMixin, Base):
    __tablename__ = "media_cleanup"
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[int] = mapped_column(index=True)
    file_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)


