"""Fail closed before creating directories on a missing production data disk."""

import asyncio
import errno
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from filelock import FileLock, Timeout

from app.core.errors import AppError


class StorageUnavailableError(AppError):
    code = "STORAGE_UNAVAILABLE"
    status_code = 503
    message = "服务端存储不可用或剩余空间不足，请联系管理员；未继续写入"


@contextmanager
def storage_reservation(settings, incoming_bytes, *, target=None):
    """Reserve prospective bytes across local processes; kernel locks expire on exit."""
    if settings.runtime_execution_location != "cloud":
        yield
        return
    if incoming_bytes < 0:
        raise StorageUnavailableError()
    require_storage_capacity(settings, incoming_bytes, target=target)
    config = getattr(settings, "storage_config_path", settings.storage_path.parent / "config.json")
    directory = config.parent / "write-budgets"
    claimed = None
    registered = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise StorageUnavailableError()
        # Persistent lock filenames are never unlinked: removing a live inode
        # would allow two processes to acquire different locks for one slot.
        with FileLock(directory / "registry.lock", timeout=.1):
            reserved = 0
            for number in range(64):
                lock = FileLock(directory / f"{number}.lock", thread_local=False)
                amount = directory / f"{number}.bytes"
                try:
                    lock.acquire(timeout=0)
                except Timeout:
                    value = int(amount.read_text(encoding="ascii"))
                    if value < 0:
                        raise StorageUnavailableError()
                    reserved += value
                else:
                    if claimed is None:
                        claimed = lock
                        record = amount
                    else:
                        lock.release()
            if claimed is None:
                raise StorageUnavailableError()
            require_storage_capacity(settings, incoming_bytes + reserved, target=target)
            record.write_text(str(incoming_bytes), encoding="ascii")
        registered = True
        yield
    except (OSError, ValueError) as exc:
        if registered and getattr(exc, "errno", None) not in {errno.ENOSPC, errno.EDQUOT}:
            raise
        raise StorageUnavailableError() from exc
    finally:
        if claimed is not None:
            claimed.release()


async def finish_storage_io(operation, *args):
    """Drain an in-flight file write before the caller cleans up on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


def write_storage_bytes(settings, target: Path, data: bytes) -> None:
    with storage_reservation(settings, len(data), target=target):
        _write_storage_bytes(settings, target, data)


def _write_storage_bytes(settings, target: Path, data: bytes) -> None:
    """Publish complete bytes only; leave an existing destination intact on failure."""
    require_storage_capacity(settings, len(data), target=target)
    temporary = target.with_name(f".{uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise StorageUnavailableError() from exc
        raise
    finally:
        temporary.unlink(missing_ok=True)


def copy_storage_file(settings, source: Path, target: Path) -> None:
    with storage_reservation(settings, source.stat().st_size, target=target):
        _copy_storage_file(settings, source, target)


def _copy_storage_file(settings, source: Path, target: Path) -> None:
    """Bound memory and publish only a complete copy, even when the disk fills."""
    require_storage_capacity(settings, source.stat().st_size, target=target)
    temporary = target.with_name(f".{uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                require_storage_capacity(settings, len(chunk), target=target)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise StorageUnavailableError() from exc
        raise
    finally:
        temporary.unlink(missing_ok=True)


def require_storage_capacity(settings, incoming_bytes: int = 0, target: Path | None = None) -> None:
    if settings.runtime_execution_location != "cloud" and settings.storage_required_mount is None:
        return
    try:
        validate_storage_startup(settings)
        root = settings.storage_path.resolve()
        if not root.is_dir() or (target is not None and not target.resolve().is_relative_to(root)):
            raise StorageUnavailableError()
        if shutil.disk_usage(root).free < settings.storage_min_free_bytes + max(0, incoming_bytes):
            raise StorageUnavailableError()
    except (OSError, RuntimeError) as exc:
        raise StorageUnavailableError() from exc


def validate_storage_startup(settings) -> None:
    mount = settings.storage_required_mount
    if mount is None:
        if settings.is_production and settings.runtime_execution_location == "cloud":
            raise RuntimeError("Cloud production requires STORAGE_REQUIRED_MOUNT")
        return
    mount = Path(mount)
    if not mount.is_absolute() or mount.is_symlink():
        raise RuntimeError("STORAGE_REQUIRED_MOUNT must be an absolute mount point")
    mount = mount.resolve()
    if mount == Path(mount.anchor) or not mount.is_mount():
        raise RuntimeError("Required data disk is not mounted; refusing startup")
    for target in (settings.storage_path, settings.log_path, settings.storage_config_path):
        if not Path(target).resolve().is_relative_to(mount):
            raise RuntimeError("Storage, logs and storage configuration must reside on the required data disk")
    if shutil.disk_usage(mount).free < settings.storage_min_free_bytes:
        raise RuntimeError("Data disk free space is below STORAGE_MIN_FREE_BYTES; refusing startup")
