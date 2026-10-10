"""One background loop per database (PostgreSQL) or installation (SQLite)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from filelock import FileLock, Timeout
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import engine

logger = logging.getLogger(__name__)
LOCK_KEYS = {"canvas_workflow": 76422002, "storage_mirror": 76422003,
             "bootstrap": 76422004, "storage_cleanup": 76422005, "audio_result_cleanup": 76422006}
# Long-lived leadership connections must not consume the business pool's slots.
_coordination_engine = (
    create_async_engine(settings.database_url, poolclass=NullPool,
                        connect_args={"timeout": 5, "command_timeout": 5})
    if engine.dialect.name == "postgresql" else None
)


@asynccontextmanager
async def leadership(name: str):
    key = LOCK_KEYS[name]
    if engine.dialect.name != "postgresql":
        directory = settings.storage_config_path.parent / "background-locks"
        directory.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(directory / f"{name}.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            yield None
            return
        try:
            yield lambda: asyncio.sleep(0)
        finally:
            lock.release()
        return

    async with _coordination_engine.connect() as connection:
        acquired = False
        try:
            acquired = bool(await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
            await connection.commit()
            if not acquired:
                yield None
                return
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection

            async def check():
                # Never reconnect transparently: a new connection would not own
                # the session lock. Invalidation must stop this leader first.
                if connection.invalidated:
                    raise ConnectionError("Background leadership connection lost")
                async def roundtrip():
                    # Probe the same lock-owning physical connection directly.
                    # The watchdog owns the deadline; driver query cancellation
                    # would otherwise wait for a second connection on partitions.
                    try:
                        await driver.fetchval("SELECT 1", timeout=60)
                    except Exception as exc:
                        raise ConnectionError("Background leadership connection lost") from exc

                probe = asyncio.create_task(roundtrip())
                try:
                    done, _ = await asyncio.wait((probe,), timeout=5)
                    if not done:
                        # asyncpg query cancellation itself needs a working
                        # network. A transport blackhole requires hard teardown.
                        driver.terminate()
                        # Termination wakes the pending query; cancelling it as
                        # well can interrupt SQLAlchemy's disconnect cleanup.
                        await asyncio.gather(probe, return_exceptions=True)
                        raise ConnectionError("Background leadership probe timed out")
                    await probe
                except asyncio.CancelledError:
                    if not probe.done():
                        driver.terminate()
                        await asyncio.gather(probe, return_exceptions=True)
                    raise

            yield check
        finally:
            if acquired:
                # Physically close instead of returning a session-level lock to
                # the pool, including on cancellation and database errors.
                await connection.invalidate()


async def _serve(callback: Callable[[], Awaitable[None]], check, heartbeat_seconds):
    async def heartbeat():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            await check()

    work = asyncio.create_task(callback())
    monitor = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait((work, monitor), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        work.cancel()
        monitor.cancel()
        await asyncio.gather(work, monitor, return_exceptions=True)


async def run_singleton(name: str, callback: Callable[[], Awaitable[None]], *,
                        retry_seconds: float = 5, heartbeat_seconds: float = 2):
    if name not in LOCK_KEYS:
        raise ValueError("Unknown background service")
    while True:
        try:
            async with leadership(name) as check:
                if check is not None:
                    await _serve(callback, check, heartbeat_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception strings can contain connection URLs or credentials.
            logger.warning("Background service %s relinquished leadership (%s)", name, type(exc).__name__)
        await asyncio.sleep(retry_seconds)


async def initialize_once_at_a_time(callback: Callable[[], Awaitable[None]]):
    while True:
        async with leadership("bootstrap") as check:
            if check is not None:
                await _serve(callback, check, 2)
                return
        await asyncio.sleep(.1)
