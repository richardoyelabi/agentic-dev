"""Tests for custom logging handlers (file and dashboard)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock


from agentic_dev.logging.events import LogEvent, AgentStartEvent
from agentic_dev.logging.handlers import (
    HumanReadableFileHandler,
    JSONLinesFileHandler,
    RichDashboardHandler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_event(handler: logging.Handler, event: LogEvent) -> None:
    """Create a log record with an attached event and emit it through the handler."""
    record = logging.LogRecord(
        name="agentic_dev.events.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg=event.message,
        args=(),
        exc_info=None,
    )
    record.event = event  # type: ignore[attr-defined]
    handler.emit(record)


# ---------------------------------------------------------------------------
# JSONLinesFileHandler
# ---------------------------------------------------------------------------


class TestJSONLinesFileHandler:
    def test_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        filepath = tmp_path / "nested" / "deep" / "events.jsonl"
        handler = JSONLinesFileHandler(filepath)
        try:
            assert filepath.parent.exists()
            # Emit one event so the file is created by the file handler
            _emit_event(handler, LogEvent(message="init", event_type="test"))
            assert filepath.exists()
        finally:
            handler.close()

    def test_writes_one_json_object_per_line(self, tmp_path: Path) -> None:
        filepath = tmp_path / "events.jsonl"
        handler = JSONLinesFileHandler(filepath)
        try:
            _emit_event(handler, LogEvent(message="first", event_type="t1"))
            _emit_event(handler, LogEvent(message="second", event_type="t2"))
            _emit_event(handler, LogEvent(message="third", event_type="t3"))
            handler.flush()

            lines = filepath.read_text().strip().splitlines()
            assert len(lines) == 3

            for line in lines:
                parsed = json.loads(line)
                assert "event_type" in parsed
                assert "message" in parsed
        finally:
            handler.close()

    def test_preserves_subclass_fields(self, tmp_path: Path) -> None:
        filepath = tmp_path / "events.jsonl"
        handler = JSONLinesFileHandler(filepath)
        try:
            event = AgentStartEvent(
                message="agent go",
                agent_name="fe_dev",
                model="sonnet",
                prompt_length=300,
                working_dir="/work",
            )
            _emit_event(handler, event)
            handler.flush()

            parsed = json.loads(filepath.read_text().strip())
            assert parsed["agent_name"] == "fe_dev"
            assert parsed["model"] == "sonnet"
        finally:
            handler.close()


# ---------------------------------------------------------------------------
# HumanReadableFileHandler
# ---------------------------------------------------------------------------


class TestHumanReadableFileHandler:
    def test_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        filepath = tmp_path / "sub" / "pipeline.log"
        handler = HumanReadableFileHandler(filepath)
        try:
            _emit_event(handler, LogEvent(message="init", event_type="test"))
            assert filepath.exists()
        finally:
            handler.close()

    def test_writes_human_readable_lines(self, tmp_path: Path) -> None:
        filepath = tmp_path / "pipeline.log"
        handler = HumanReadableFileHandler(filepath)
        try:
            _emit_event(handler, LogEvent(message="started", event_type="pipeline_start"))
            _emit_event(handler, LogEvent(message="done", event_type="pipeline_complete"))
            handler.flush()

            content = filepath.read_text()
            lines = content.strip().splitlines()
            assert len(lines) == 2

            # Each line should start with a bracketed timestamp
            for line in lines:
                assert line.startswith("[")
                assert "]" in line
        finally:
            handler.close()

    def test_lines_contain_event_type_and_message(self, tmp_path: Path) -> None:
        filepath = tmp_path / "pipeline.log"
        handler = HumanReadableFileHandler(filepath)
        try:
            _emit_event(
                handler,
                LogEvent(message="checking stuff", event_type="my_check"),
            )
            handler.flush()

            content = filepath.read_text()
            assert "my_check" in content
            assert "checking stuff" in content
        finally:
            handler.close()


# ---------------------------------------------------------------------------
# RichDashboardHandler
# ---------------------------------------------------------------------------


class TestRichDashboardHandler:
    def test_calls_dashboard_update_with_event(self) -> None:
        mock_dashboard = MagicMock()
        handler = RichDashboardHandler(mock_dashboard)

        event = LogEvent(message="ping", event_type="test")
        _emit_event(handler, event)

        mock_dashboard.update.assert_called_once_with(event)

    def test_does_not_call_update_without_event(self) -> None:
        mock_dashboard = MagicMock()
        handler = RichDashboardHandler(mock_dashboard)

        record = logging.LogRecord(
            name="agentic_dev.events.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="no event attached",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        mock_dashboard.update.assert_not_called()

    def test_multiple_events_call_update_each_time(self) -> None:
        mock_dashboard = MagicMock()
        handler = RichDashboardHandler(mock_dashboard)

        for i in range(3):
            _emit_event(
                handler,
                LogEvent(message=f"event-{i}", event_type="test"),
            )

        assert mock_dashboard.update.call_count == 3
