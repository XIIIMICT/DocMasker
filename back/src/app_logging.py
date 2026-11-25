"""
Structured application logging: JSON Lines to a rotating file + human stderr.

Only a fixed allow-list of structured fields is serialized. Inputs, keys,
entity values, filenames, paths, SQL text and raw exception text are never
logged; failures are recorded by exception type only. Rotating the file at
64 MB x 5 bounds the disk usage while keeping several days of history.
"""

import json
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

DEFAULT_LOG_FILE = "logs/docmasker.log"
LOG_MAX_BYTES = 64 * 1024 * 1024
LOG_BACKUP_COUNT = 5

#: Correlation id shared by the error middleware, request logs and events
#: emitted deeper in a request. It is an opaque uuid4, never user data.
REQUEST_ID: ContextVar[str | None] = ContextVar("docmasker_request_id", default=None)

_SAFE_FIELDS: tuple[str, ...] = (
    "event",
    "request_id",
    "job_id",
    "worker_id",
    "operation",
    "error_code",
    "error_type",
    "outcome",
    "method",
    "route",
    "status_code",
    "kind",
    "attempt",
    "duration_ms",
    "queue_wait_ms",
    "port",
    "storage",
    "dependency",
)


@contextmanager
def bind_request_id(request_id: str) -> Generator[None]:
    """Bind a request id to all structured events emitted in this context."""
    token = REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        REQUEST_ID.reset(token)


class JsonFormatter(logging.Formatter):
    """Format application events as JSON Lines for Loki."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }
        for field_name in _SAFE_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        # No traceback/exception text: type-only logging is the contract.
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_application_logging(log_file: str = DEFAULT_LOG_FILE) -> None:
    """Install the JSON file handler and the human-readable stderr handler once."""
    logger = logging.getLogger("docmasker")
    if logger.handlers:
        return

    destination = Path(log_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        destination,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(stderr_handler)
    logger.setLevel(logging.INFO)
    # Root handlers (if any) must not duplicate stderr lines.
    logger.propagate = False


def log_event(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record a structured INFO event without boilerplate at call sites."""
    logger.info(message, extra={"event": event, **fields})


def log_warning(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record a structured warning."""
    logger.warning(message, extra={"event": event, **fields})


def log_error(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    """Record a structured error."""
    logger.error(message, extra={"event": event, **fields})
