"""Versioned execution policy managed by administrators."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ExecutionPolicy(TimestampMixin, Base):
    __tablename__ = "execution_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
