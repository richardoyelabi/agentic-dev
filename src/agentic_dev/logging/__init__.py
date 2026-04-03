"""Structured logging system for the agentic-dev pipeline.

Public API
----------
- ``setup_logging(run_id, project_name, log_dir, console)`` — configure
  handlers for a pipeline run.
- ``teardown_logging()`` — flush/close handlers and stop the dashboard.
- ``get_event_logger(name)`` — return a logger under the event hierarchy.
- ``emit(logger, event)`` — emit a structured ``LogEvent`` as a log record.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_dev.logging.context import RunContext, get_run_context, set_run_context
from agentic_dev.logging.events import LogEvent

if TYPE_CHECKING:
    import contextvars

    from rich.console import Console

    from agentic_dev.logging.dashboard import PipelineDashboard

EVENT_LOGGER_ROOT = "agentic_dev.events"

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

# Module-level state for the current logging session
_dashboard: "PipelineDashboard | None" = None
_handlers: list[logging.Handler] = []
_context_token: contextvars.Token[RunContext | None] | None = None


def generate_run_id() -> str:
    """Generate a 12-character hex run ID from UUID4."""
    return uuid.uuid4().hex[:12]


def get_event_logger(name: str) -> logging.Logger:
    """Return a logger under the ``agentic_dev.events`` hierarchy."""
    return logging.getLogger(f"{EVENT_LOGGER_ROOT}.{name}")


def emit(logger: logging.Logger, event: LogEvent) -> None:
    """Emit a structured event as a log record.

    Populates ``run_id`` and ``project_name`` from the current
    ``RunContext`` before logging.
    """
    ctx = get_run_context()
    if ctx is not None:
        event.run_id = ctx.run_id
        event.project_name = ctx.project_name
    logger.log(
        _LEVEL_MAP.get(event.level, logging.INFO),
        event.message,
        extra={"event": event},
    )


def setup_logging(
    run_id: str,
    project_name: str,
    log_dir: Path,
    console: "Console | None" = None,
) -> None:
    """Configure logging handlers for a pipeline run.

    Creates a run directory under ``log_dir/runs/<run_id>/`` and attaches
    JSON lines and human-readable file handlers to the event logger root.
    If *console* is provided and stdout is a terminal, a Rich live
    dashboard handler is also attached.
    """
    global _dashboard, _context_token  # noqa: PLW0603

    from agentic_dev.config import LATEST_SYMLINK, RUNS_DIR
    from agentic_dev.logging.dashboard import PipelineDashboard
    from agentic_dev.logging.handlers import (
        HumanReadableFileHandler,
        JSONLinesFileHandler,
        RichDashboardHandler,
    )

    # Set up run context
    ctx = RunContext(run_id=run_id, project_name=project_name)
    _context_token = set_run_context(ctx)

    # Create run directory
    run_dir = log_dir / RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Update 'latest' symlink
    latest_link = log_dir / LATEST_SYMLINK
    try:
        if latest_link.is_symlink() or latest_link.exists():
            latest_link.unlink()
        latest_link.symlink_to(run_dir)
    except OSError:
        pass

    # Configure the event logger root
    root_logger = logging.getLogger(EVENT_LOGGER_ROOT)
    root_logger.setLevel(logging.DEBUG)

    # JSON lines handler
    jsonl_handler = JSONLinesFileHandler(run_dir / "events.jsonl")
    jsonl_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(jsonl_handler)
    _handlers.append(jsonl_handler)

    # Human-readable handler
    log_handler = HumanReadableFileHandler(run_dir / "pipeline.log")
    log_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(log_handler)
    _handlers.append(log_handler)

    # Rich dashboard handler (only in interactive terminals)
    if console is not None and console.is_terminal:
        _dashboard = PipelineDashboard(console)
        dash_handler = RichDashboardHandler(_dashboard)
        dash_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(dash_handler)
        _handlers.append(dash_handler)
        _dashboard.start()


def teardown_logging() -> None:
    """Flush and close all logging handlers, stop the dashboard."""
    global _dashboard, _context_token  # noqa: PLW0603

    # Stop the dashboard first
    if _dashboard is not None:
        _dashboard.stop()
        _dashboard = None

    # Remove and close all handlers
    root_logger = logging.getLogger(EVENT_LOGGER_ROOT)
    for handler in _handlers:
        root_logger.removeHandler(handler)
        handler.close()
    _handlers.clear()

    # Clear the run context
    if _context_token is not None:
        from agentic_dev.logging.context import clear_run_context

        clear_run_context(_context_token)
        _context_token = None
