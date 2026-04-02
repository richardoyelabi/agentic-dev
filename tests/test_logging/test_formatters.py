"""Tests for JSON lines and human-readable log formatters."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from agentic_dev.logging.events import LogEvent, AgentStartEvent
from agentic_dev.logging.formatters import (
    HumanReadableFormatter,
    JSONLinesFormatter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    event: LogEvent | None = None,
    message: str = "test message",
    level: int = logging.INFO,
) -> logging.LogRecord:
    """Create a LogRecord, optionally attaching a structured event."""
    record = logging.LogRecord(
        name="agentic_dev.events.test",
        level=level,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    # Populate record.message the same way Formatter.format() does internally;
    # formatMessage() relies on this attribute existing.
    record.message = record.getMessage()
    if event is not None:
        record.event = event  # type: ignore[attr-defined]
    return record


# ---------------------------------------------------------------------------
# JSONLinesFormatter
# ---------------------------------------------------------------------------


class TestJSONLinesFormatter:
    def test_format_with_event_produces_valid_json(self) -> None:
        event = LogEvent(
            message="structured log",
            event_type="test_event",
            level="INFO",
        )
        record = _make_record(event=event)
        formatter = JSONLinesFormatter()

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["event_type"] == "test_event"
        assert parsed["message"] == "structured log"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_format_with_subclass_event_preserves_extra_fields(self) -> None:
        event = AgentStartEvent(
            message="agent starting",
            agent_name="backend_developer",
            model="opus",
            prompt_length=500,
            working_dir="/tmp/work",
        )
        record = _make_record(event=event)
        formatter = JSONLinesFormatter()

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["event_type"] == "agent_start"
        assert parsed["agent_name"] == "backend_developer"
        assert parsed["model"] == "opus"
        assert parsed["prompt_length"] == 500

    def test_format_without_event_produces_fallback_json(self) -> None:
        record = _make_record(message="plain log")
        formatter = JSONLinesFormatter()

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["event_type"] == "log_message"
        assert parsed["message"] == "plain log"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed
        assert "logger" in parsed

    def test_format_fallback_includes_logger_name(self) -> None:
        record = _make_record(message="check logger")
        formatter = JSONLinesFormatter()

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["logger"] == "agentic_dev.events.test"

    def test_format_output_is_single_line(self) -> None:
        event = LogEvent(message="no newlines", event_type="test")
        record = _make_record(event=event)
        formatter = JSONLinesFormatter()

        result = formatter.format(record)
        assert "\n" not in result


# ---------------------------------------------------------------------------
# HumanReadableFormatter
# ---------------------------------------------------------------------------


class TestHumanReadableFormatter:
    def test_format_with_event_has_expected_structure(self) -> None:
        ts = datetime(2026, 4, 1, 14, 30, 45, tzinfo=timezone.utc)
        event = LogEvent(
            message="something happened",
            event_type="test_event",
            level="WARNING",
            timestamp=ts,
        )
        record = _make_record(event=event)
        formatter = HumanReadableFormatter()

        result = formatter.format(record)

        assert result.startswith("[14:30:45]")
        assert "WARNING" in result
        assert "test_event" in result
        assert "something happened" in result

    def test_format_with_event_follows_pattern(self) -> None:
        """Output should follow: [HH:MM:SS] LEVEL   event_type   message"""
        ts = datetime(2026, 1, 15, 8, 5, 3, tzinfo=timezone.utc)
        event = LogEvent(
            message="msg",
            event_type="my_type",
            level="INFO",
            timestamp=ts,
        )
        record = _make_record(event=event)
        formatter = HumanReadableFormatter()

        result = formatter.format(record)

        # Verify the timestamp comes first
        assert result.startswith("[08:05:03]")
        # Verify all pieces are present in order
        ts_idx = result.index("[08:05:03]")
        level_idx = result.index("INFO")
        type_idx = result.index("my_type")
        msg_idx = result.index("msg", type_idx)
        assert ts_idx < level_idx < type_idx < msg_idx

    def test_format_without_event_produces_fallback(self) -> None:
        record = _make_record(message="plain fallback")
        formatter = HumanReadableFormatter()

        result = formatter.format(record)

        assert "log_message" in result
        assert "plain fallback" in result
        # Should still have a timestamp bracket
        assert result.startswith("[")
        assert "]" in result

    def test_fallback_includes_level(self) -> None:
        record = _make_record(message="level check", level=logging.WARNING)
        formatter = HumanReadableFormatter()

        result = formatter.format(record)

        assert "WARNING" in result
