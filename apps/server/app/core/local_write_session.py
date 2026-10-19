"""Acquire SQLite write ownership before reading a mutable task snapshot."""

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal

_writer_locks: WeakKeyDictionary = WeakKeyDictionary()


def sqlite_busy(exc: OperationalError) -> bool:
    code = getattr(exc.orig, "sqlite_errorcode", None)
    return (code is not None and (code & 255) in {5, 6}) or "database is locked" in str(exc.orig).lower()


@asynccontextmanager
async def local_write_session():
    session = SessionLocal()
    sqlite = session.get_bind().dialect.name == "sqlite"
    await session.close()
    if not sqlite:
        async with SessionLocal() as session:
            yield session
        return
    # FIFO admission prevents one task starving behind repeated scheduler writes.
    # Only local transactions enter this queue, never network generation calls.
    loop = asyncio.get_running_loop()
    lock = _writer_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        async with _sqlite_write_session() as session:
            yield session


@asynccontextmanager
async def _sqlite_write_session():
    # Retry lock acquisition only. Never replay the caller's work or a provider request.
    for attempt in range(6):
        session = SessionLocal()
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
        except BaseException as exc:
            await session.close()
            if not isinstance(exc, OperationalError) or not sqlite_busy(exc) or attempt == 5:
                raise
            await asyncio.sleep(min(0.1 * 2 ** attempt, 1))
            continue
        async with session:
            yield session
        return
