"""Append-only request identities and receipt entries, independent of job deletion."""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, utcnow


class BillingCall(IdMixin, WorkspaceScoped, Base):
    __tablename__ = "billing_calls"
    call_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    provider_id: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(128))
    model_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[str | None] = mapped_column(String(100))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    meter: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(512), default="响应未确认，不代表免费")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingReceipt(IdMixin, WorkspaceScoped, Base):
    __tablename__ = "billing_receipts"
    __table_args__ = (UniqueConstraint("provider_id", "reference", name="uq_billing_receipt_reference"),)
    call_id: Mapped[int] = mapped_column(ForeignKey("billing_calls.id", ondelete="RESTRICT"), index=True)
    provider_id: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(200))
    amount: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(1000))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
