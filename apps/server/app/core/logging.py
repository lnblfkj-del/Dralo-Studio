"""Standalone rotating files; cloud stdout managed by service log rotation.

Model credentials must never be written in plaintext.
"""

import logging
import logging.handlers
from pathlib import Path

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


class AccessQueryRedactionFilter(logging.Filter):
    """Uvicorn logs full request targets; signed media URLs contain credentials."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            if isinstance(target, str) and "?" in target:
                record.args = (address, method, target.split("?", 1)[0] + "?[REDACTED]", version, status)
        return True


class HttpQueryRedactionFilter(logging.Filter):
    """httpx logs URL objects, including private object-store signatures."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(str(arg).split("?", 1)[0] + "?[REDACTED]"
                                if "://" in str(arg) and "?" in str(arg) else arg
                                for arg in record.args)
        return True


def _build_file_handler(log_dir: Path, filename: str) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        log_dir / filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    return handler


def setup_logging() -> None:
    """初始化日志。重复调用不会叠加 handler。"""
    global _configured
    if _configured:
        return

    log_dir = settings.log_path
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)
    cloud = settings.runtime_execution_location == "cloud"
    # Cloud API/Worker processes share service stdout; do not race on file rotation.
    if not cloud:
        root.addHandler(_build_file_handler(log_dir, "app.log"))
    logging.getLogger("uvicorn.access").addFilter(AccessQueryRedactionFilter())
    logging.getLogger("httpx").addFilter(HttpQueryRedactionFilter())
    # SDK debug messages contain authorization/signature internals.
    for name in ("botocore", "boto3", "urllib3", "alibabacloud_oss_v2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Only standalone mode keeps separate task and provider files.
    for logger_name, filename in (("app.jobs", "jobs.log"), ("app.provider", "provider.log")):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        if not cloud:
            logger.addHandler(_build_file_handler(log_dir, filename))
        logger.propagate = cloud

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """掩码敏感值，仅保留末尾若干位。用于日志与接口返回。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
