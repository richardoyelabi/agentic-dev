"""Log formatters for structured and human-readable output."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JSONLinesFormatter(logging.Formatter):
    """Formats LogRecords as single-line JSON objects for .jsonl output."""

    def format(self, record: logging.LogRecord) -> str:
        from agentic_dev.logging.events import LogEvent

        event: LogEvent | None = getattr(record, "event", None)
        if event is not None:
            return event.model_dump_json()
        # Fallback for non-structured log records
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "event_type": "log_message",
            "level": record.levelname,
            "message": self.formatMessage(record),
            "logger": record.name,
        })


class HumanReadableFormatter(logging.Formatter):
    """Traditional text format: [HH:MM:SS] LEVEL   event_type   message"""

    def format(self, record: logging.LogRecord) -> str:
        from agentic_dev.logging.events import LogEvent

        event: LogEvent | None = getattr(record, "event", None)
        if event is not None:
            ts = event.timestamp.strftime("%H:%M:%S")
            return f"[{ts}] {event.level:<7} {event.event_type:<25} {event.message}"
        # Fallback
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        return f"[{ts}] {record.levelname:<7} {'log_message':<25} {record.getMessage()}"
