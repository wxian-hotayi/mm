"""Structured logging for WealthOS.

All application loggers live under the ``wealthos`` namespace and emit
single-line JSON records, configured once via :func:`setup_logging`.
The level is taken from the ``LOG_LEVEL`` environment variable (via settings)
unless explicitly overridden.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings

ROOT_LOGGER_NAME = "wealthos"

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_")
        }
        if extras:
            payload["context"] = extras
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """Configure the ``wealthos`` logger tree exactly once (idempotent)."""
    resolved_level = (level or get_settings().LOG_LEVEL).upper()
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(resolved_level)
    root.propagate = False
    if not any(
        isinstance(handler.formatter, StructuredFormatter)
        for handler in root.handlers
    ):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(StructuredFormatter())
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``wealthos.*`` namespace."""
    if name == ROOT_LOGGER_NAME or name.startswith(f"{ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
