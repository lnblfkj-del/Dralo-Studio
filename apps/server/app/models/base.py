"""模型公共基类与混入。"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """带时区的当前时间。SQLite 不保留 tzinfo，统一在应用层处理。"""
    return datetime.now(UTC)


class IdMixin:
    """自增主键。"""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    """创建与更新时间。

    default / onupdate 使用 Python 端可调用对象而非纯服务端表达式：
    若只用 onupdate=func.now()，UPDATE 后该列会被标记为过期，异步会话中序列化
    时会触发隐式 IO 并抛 MissingGreenlet。server_default 保留，用于在应用之外
    直接写库的场景。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
