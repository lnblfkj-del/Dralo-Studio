"""Reclaim stale upload staging files only; never infer final-file ownership."""

import re
import time
from hashlib import sha256
from itertools import islice

from filelock import FileLock, Timeout

from app.core.config import settings


def upload_lock(directory):
    locks = settings.storage_config_path.parent / "upload-locks"
    locks.mkdir(exist_ok=True)
    key = sha256(str(directory.resolve()).encode()).hexdigest()
    return FileLock(locks / f"{key}.lock", thread_local=False)


def cleanup_upload_temps():
    root = settings.storage_path.resolve() / "users"
    if root.is_symlink() or not root.is_dir():
        return 0
    cutoff = time.time() - 86400
    removed = 0
    examined = 0
    for owner in islice(root.iterdir(), 10000):
        examined += 1
        if examined >= 10000:
            break
        if not owner.name.isdigit() or owner.is_symlink() or not owner.is_dir():
            continue
        directory = owner / "uploads"
        if directory.is_symlink() or not directory.is_dir():
            continue
        lock = upload_lock(directory)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            continue
        try:
            for path in islice(directory.iterdir(), 10000):
                examined += 1
                if examined >= 10000:
                    break
                if not re.fullmatch(r"\.[a-f0-9]{32}\.upload", path.name) or path.is_symlink():
                    continue
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        finally:
            lock.release()
    return removed
