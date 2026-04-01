"""Custom logging handlers for file output and Rich dashboard."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_dev.logging.formatters import HumanReadableFormatter, JSONLinesFormatter

if TYPE_CHECKING:
    from agentic_dev.logging.dashboard import PipelineDashboard


class JSONLinesFileHandler(logging.FileHandler):
    """Writes one JSON object per line to a .jsonl file."""

    def __init__(self, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(filepath), encoding="utf-8")
        self.setFormatter(JSONLinesFormatter())


class HumanReadableFileHandler(logging.FileHandler):
    """Writes human-readable log lines to a .log file."""

    def __init__(self, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(filepath), encoding="utf-8")
        self.setFormatter(HumanReadableFormatter())


class RichDashboardHandler(logging.Handler):
    """Updates the Rich live dashboard from log events."""

    def __init__(self, dashboard: PipelineDashboard) -> None:
        super().__init__()
        self._dashboard = dashboard

    def emit(self, record: logging.LogRecord) -> None:
        from agentic_dev.logging.events import LogEvent

        event: LogEvent | None = getattr(record, "event", None)
        if event is not None:
            self._dashboard.update(event)
