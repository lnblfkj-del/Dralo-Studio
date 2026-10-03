from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin

class AuditEvent(IdMixin, TimestampMixin, Base):
    __tablename__="audit_events"
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
