"""Tests for the logging setup/teardown lifecycle and public API."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import agentic_dev.logging as log_module
from agentic_dev.logging import (
    EVENT_LOGGER_ROOT,
    emit,
    generate_run_id,
    get_event_logger,
    setup_logging,
    teardown_logging,
)
from agentic_dev.logging.context import get_run_context, RunContext, set_run_context, clear_run_context
from agentic_dev.logging.events import LogEvent


# ---------------------------------------------------------------------------
# Fixture: clean logging state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_logging_state():
    """Ensure handlers and context are cleaned up after every test."""
    yield
    # Force teardown of any leftover state
    teardown_logging()
    # Also ensure the root event logger has no stale handlers
    root = logging.getLogger(EVENT_LOGGER_ROOT)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()


# ---------------------------------------------------------------------------
# generate_run_id
# ---------------------------------------------------------------------------


class TestGenerateRunId:
    def test_returns_12_char_hex_string(self) -> None:
        run_id = generate_run_id()
        assert len(run_id) == 12
        # Should only contain valid hex characters
        int(run_id, 16)

    def test_returns_unique_values(self) -> None:
        ids = {generate_run_id() for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# get_event_logger
# ---------------------------------------------------------------------------


class TestGetEventLogger:
    def test_returns_logger_under_events_hierarchy(self) -> None:
        logger = get_event_logger("my_agent")
        assert logger.name == f"{EVENT_LOGGER_ROOT}.my_agent"

    def test_returned_logger_is_stdlib_logger(self) -> None:
        logger = get_event_logger("test")
        assert isinstance(logger, logging.Logger)

    def test_different_names_return_different_loggers(self) -> None:
        a = get_event_logger("alpha")
        b = get_event_logger("beta")
        assert a is not b
        assert a.name != b.name


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_creates_run_directory(self, tmp_path: Path) -> None:
        setup_logging("run123", "my-project", tmp_path)

        run_dir = tmp_path / "runs" / "run123"
        assert run_dir.is_dir()

    def test_creates_latest_symlink(self, tmp_path: Path) -> None:
        setup_logging("run456", "my-project", tmp_path)

        latest = tmp_path / "latest"
        assert latest.is_symlink()
        assert latest.resolve() == (tmp_path / "runs" / "run456").resolve()

    def test_latest_symlink_updated_on_second_setup(self, tmp_path: Path) -> None:
        setup_logging("run_a", "proj", tmp_path)
        teardown_logging()

        setup_logging("run_b", "proj", tmp_path)

        latest = tmp_path / "latest"
        assert latest.resolve() == (tmp_path / "runs" / "run_b").resolve()

    def test_attaches_handlers_to_event_logger_root(self, tmp_path: Path) -> None:
        setup_logging("run789", "proj", tmp_path)

        root = logging.getLogger(EVENT_LOGGER_ROOT)
        # At minimum: JSONLines + HumanReadable file handlers
        assert len(root.handlers) >= 2

    def test_creates_events_jsonl_file(self, tmp_path: Path) -> None:
        setup_logging("runX", "proj", tmp_path)

        # Emit an event to ensure the file gets written
        logger = get_event_logger("setup_test")
        emit(logger, LogEvent(message="hello", event_type="test"))

        jsonl_path = tmp_path / "runs" / "runX" / "events.jsonl"
        assert jsonl_path.exists()

    def test_creates_pipeline_log_file(self, tmp_path: Path) -> None:
        setup_logging("runY", "proj", tmp_path)

        logger = get_event_logger("setup_test")
        emit(logger, LogEvent(message="hello", event_type="test"))

        log_path = tmp_path / "runs" / "runY" / "pipeline.log"
        assert log_path.exists()

    def test_sets_run_context(self, tmp_path: Path) -> None:
        setup_logging("ctx_run", "ctx_proj", tmp_path)

        ctx = get_run_context()
        assert ctx is not None
        assert ctx.run_id == "ctx_run"
        assert ctx.project_name == "ctx_proj"


# ---------------------------------------------------------------------------
# teardown_logging
# ---------------------------------------------------------------------------


class TestTeardownLogging:
    def test_removes_all_handlers(self, tmp_path: Path) -> None:
        setup_logging("td_run", "proj", tmp_path)

        root = logging.getLogger(EVENT_LOGGER_ROOT)
        assert len(root.handlers) > 0

        teardown_logging()
        assert len(root.handlers) == 0

    def test_clears_run_context(self, tmp_path: Path) -> None:
        setup_logging("td_ctx", "proj", tmp_path)
        assert get_run_context() is not None

        teardown_logging()
        assert get_run_context() is None

    def test_teardown_without_setup_is_safe(self) -> None:
        """Calling teardown when nothing was set up should not raise."""
        teardown_logging()

    def test_clears_module_handler_list(self, tmp_path: Path) -> None:
        setup_logging("td_list", "proj", tmp_path)
        assert len(log_module._handlers) > 0

        teardown_logging()
        assert len(log_module._handlers) == 0


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


class TestEmit:
    def test_populates_run_id_from_context(self, tmp_path: Path) -> None:
        setup_logging("emit_run", "emit_proj", tmp_path)

        event = LogEvent(message="test emit", event_type="test")
        logger = get_event_logger("emit_test")
        emit(logger, event)

        assert event.run_id == "emit_run"

    def test_populates_project_name_from_context(self, tmp_path: Path) -> None:
        setup_logging("emit_run2", "my_project", tmp_path)

        event = LogEvent(message="test emit", event_type="test")
        logger = get_event_logger("emit_test")
        emit(logger, event)

        assert event.project_name == "my_project"

    def test_emit_without_context_leaves_defaults(self) -> None:
        """When no RunContext is set, run_id and project_name stay as defaults."""
        event = LogEvent(message="no context", event_type="test")
        logger = get_event_logger("emit_no_ctx")
        # Ensure no context is set
        assert get_run_context() is None

        emit(logger, event)

        assert event.run_id == ""
        assert event.project_name == ""

    def test_emit_logs_at_correct_level(self, tmp_path: Path) -> None:
        setup_logging("emit_level", "proj", tmp_path)

        logger = get_event_logger("level_test")
        with patch.object(logger, "log", wraps=logger.log) as mock_log:
            event = LogEvent(message="warn", event_type="test", level="WARNING")
            emit(logger, event)
            mock_log.assert_called_once()
            assert mock_log.call_args[0][0] == logging.WARNING

    def test_emit_attaches_event_to_record_extra(self, tmp_path: Path) -> None:
        """The event should be available in the log record's extra dict."""
        setup_logging("emit_extra", "proj", tmp_path)

        captured_records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_records.append(record)

        root = logging.getLogger(EVENT_LOGGER_ROOT)
        cap_handler = CapturingHandler()
        root.addHandler(cap_handler)
        try:
            event = LogEvent(message="capture me", event_type="test")
            logger = get_event_logger("capture_test")
            emit(logger, event)

            assert len(captured_records) == 1
            assert getattr(captured_records[0], "event", None) is event
        finally:
            root.removeHandler(cap_handler)
