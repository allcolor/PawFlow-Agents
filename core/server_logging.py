"""Server logging configuration.

The interactive server still writes to stdout/stderr for container runtimes, but
also mirrors logs to bounded files so recent diagnostics are available after a
restart without copying terminal output.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid %s=%r; using %d", name, raw, default)
        return default


def _server_log_dir() -> Path:
    custom = os.getenv("PAWFLOW_SERVER_LOG_DIR")
    if custom:
        return Path(custom)
    from core.paths import RUNTIME_DIR
    return RUNTIME_DIR / "logs"


def _remove_existing_file_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if not getattr(handler, "_pawflow_server_file_handler", False):
            continue
        root.removeHandler(handler)
        handler.close()


def _rotating_handler(path: Path, *, level: int, max_bytes: int,
                      backup_count: int,
                      formatter: logging.Formatter) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=max(1, max_bytes),
        backupCount=max(1, backup_count),
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler._pawflow_server_file_handler = True  # type: ignore[attr-defined]
    return handler


def configure_server_logging(level: int = logging.INFO) -> None:
    """Configure console logging plus bounded server log files.

    Files are rotated by size. `backupCount` is the retention window for old
    segments: `server.log`, `server.log.1`, ... and `server.error.log.*`.
    """
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    root = logging.getLogger()
    if root.level > level:
        root.setLevel(level)

    _remove_existing_file_handlers(root)

    log_dir = _server_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = _env_int("PAWFLOW_SERVER_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
    backup_count = _env_int("PAWFLOW_SERVER_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT)
    error_backup_count = _env_int(
        "PAWFLOW_SERVER_ERROR_LOG_BACKUP_COUNT", backup_count)

    root.addHandler(_rotating_handler(
        log_dir / "server.log",
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        formatter=formatter,
    ))
    root.addHandler(_rotating_handler(
        log_dir / "server.error.log",
        level=logging.ERROR,
        max_bytes=max_bytes,
        backup_count=error_backup_count,
        formatter=formatter,
    ))
