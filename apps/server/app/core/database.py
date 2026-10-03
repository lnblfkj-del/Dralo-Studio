"""数据库引擎与会话。

SQLite 由 API 与 Worker 两个独立进程共享，因此必须在每个连接建立时固化 PRAGMA。
缺少任意一项都会在并发下出问题，见 DEVELOPMENT.md 7.1。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _ensure_sqlite_dir() -> None:
    """SQLite 文件所在目录不存在时先创建，否则连接会直接失败。"""
    sqlite_file = settings.sqlite_file
    if sqlite_file is not None:
        sqlite_file.parent.mkdir(parents=True, exist_ok=True)


def create_engine() -> AsyncEngine:
    """创建异步引擎，并为 SQLite 注册 PRAGMA 监听器。"""
    if _is_sqlite(settings.database_url):
        _ensure_sqlite_dir()

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        # SQLite 用文件锁而非连接池并发，但 API/Worker 两个进程各自维护连接池，
        # 需要合理配置避免 SSE 长连接与任务并发时耗尽。
        # pool_size: 每个进程常驻连接数
        # max_overflow: 超出 pool_size 后允许的临时连接
        # pool_recycle: 连接存活时间（秒），防止 SQLite 文件锁残留
        # pool_timeout: 获取连接超时时间（秒）
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=3600,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
    )

    if _is_sqlite(settings.database_url):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(
            dbapi_connection: DBAPIConnection,
            _connection_record: ConnectionPoolEntry,
        ) -> None:
            cursor = dbapi_connection.cursor()
            try:
                # WAL：读写不互锁，API 与 Worker 可同时访问
                cursor.execute("PRAGMA journal_mode=WAL")
                # 写冲突时等待而非立即抛 database is locked
                cursor.execute("PRAGMA busy_timeout=5000")
                # WAL 模式下的合理取舍
                cursor.execute("PRAGMA synchronous=NORMAL")
                # SQLite 默认关闭外键约束，必须显式开启
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


engine: AsyncEngine = create_engine()

class ScopedAsyncSession(AsyncSession):
    async def __aexit__(self, *args):
        from anyio import CancelScope
        # SSE uses level-triggered cancellation; releasing its connection must
        # finish even when the surrounding request scope has been cancelled.
        with CancelScope(shield=True):
            return await super().__aexit__(*args)

    async def get(self, entity, ident, **kwargs):
        value = await super().get(entity, ident, **kwargs)
        from app.models.workspace_scoped import WorkspaceScoped
        from app.core.workspace_context import isolation_enabled, _system_access, required_workspace
        if isinstance(value, WorkspaceScoped) and isolation_enabled() and not _system_access.get():
            if value.workspace_id != required_workspace().workspace_id:
                return None
        return value


SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=ScopedAsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    """FastAPI 依赖：提供一个自动关闭的会话。"""
    async with SessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    """应用关闭时释放连接。"""
    await engine.dispose()
