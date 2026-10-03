"""Worker registration and heartbeat shared by local and cloud runtimes."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import utcnow


class WorkerRuntime(Base):
    __tablename__ = "worker_runtimes"

    worker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True, server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
