"""Durable per-job output budget; job state determines whether it is held."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.workspace_scoped import WorkspaceScoped


class MediaReservation(TimestampMixin, WorkspaceScoped, Base):
    __tablename__ = "media_reservations"
    __table_args__ = (CheckConstraint("size >= 0", name="ck_media_reservation_size"),)

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
