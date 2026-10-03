"""用户模型。

按多用户结构预留，第一版只使用 admin / member 两种角色，不做复杂 RBAC。
后期 SaaS 化时在此基础上扩展租户隔离，无需重建表结构。见 PROJECT_SPEC.md 36.1。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, Integer, BigInteger, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    permissions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, default=lambda: uuid4().hex, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    contact: Mapped[str | None] = mapped_column(String(128))
    avatar_media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL", use_alter=True, name="fk_users_avatar_media_id"))
    session_reason: Mapped[str | None] = mapped_column(String(32))
    media_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role}>"
