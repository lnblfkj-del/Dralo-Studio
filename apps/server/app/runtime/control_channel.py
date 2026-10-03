"""Local file channel shared by the API and runtime supervisor."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

CONTROL_DIRECTORY = PROJECT_ROOT / "data" / "runtime-control"
COMMAND_PATH = CONTROL_DIRECTORY / "command.json"
STATE_PATH = CONTROL_DIRECTORY / "state.json"
SUPERVISOR_LOCK_PATH = PROJECT_ROOT / "data" / "runtime-supervisor.lock"


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def create_command(value: dict[str, Any]) -> None:
    """Create exactly one pending command across concurrent API requests."""
    COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(COMMAND_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(
            descriptor,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove_command() -> None:
    COMMAND_PATH.unlink(missing_ok=True)


def supervisor_pid() -> int | None:
    try:
        return int(SUPERVISOR_LOCK_PATH.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        from app.runtime.windows_process import process_alive as windows_process_alive

        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def supervisor_available() -> bool:
    pid = supervisor_pid()
    return bool(pid and process_alive(pid))
