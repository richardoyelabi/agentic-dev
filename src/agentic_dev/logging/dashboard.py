"""Rich live dashboard for pipeline progress monitoring."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from agentic_dev.logging.events import LogEvent


@dataclass
class DashboardState:
    """Mutable state backing the live dashboard display."""

    current_phase: str = "IDLE"
    active_agent: str | None = None
    sprint_current: int | None = None
    sprint_total: int | None = None
    total_cost_usd: float = 0.0
    mode: str = "new"
    start_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    recent_events: deque[str] = field(
        default_factory=lambda: deque(maxlen=50)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class PipelineDashboard:
    """Rich Live display showing pipeline progress and an event log.

    The dashboard is split into two panels:
    1. **Status panel** -- current phase, active agent, sprint progress,
       cumulative cost, and elapsed time.
    2. **Event log** -- scrolling list of the most recent events.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._state = DashboardState()
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create and start the Rich Live display."""
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=2,
            vertical_overflow="visible",
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the Rich Live display if it is active."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def update(self, event: LogEvent) -> None:
        """Apply a log event to the dashboard state and refresh."""
        with self._state._lock:
            self._apply_event(event)
            self._state.recent_events.append(self._format_event_line(event))

        if self._live is not None:
            self._live.update(self._render())

    def _apply_event(self, event: LogEvent) -> None:
        """Update internal state fields based on event type.

        Must be called while holding ``self._state._lock``.
        """
        etype = event.event_type

        if etype == "phase_transition":
            self._state.current_phase = event.to_phase  # type: ignore[attr-defined]

        elif etype == "agent_start":
            self._state.active_agent = event.agent_name  # type: ignore[attr-defined]

        elif etype == "agent_complete":
            self._state.active_agent = None
            self._state.total_cost_usd += event.cost_usd  # type: ignore[attr-defined]

        elif etype == "agent_failed":
            self._state.active_agent = None

        elif etype == "sprint_start":
            self._state.sprint_current = event.sprint_number  # type: ignore[attr-defined]

        elif etype in ("sprint_complete", "sprint_failed"):
            self._state.sprint_current = None

        elif etype == "pipeline_start":
            self._state.mode = event.mode  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_event_line(event: LogEvent) -> str:
        """Return ``[HH:MM:SS] message`` for the event."""
        ts = event.timestamp.strftime("%H:%M:%S")
        return f"[{ts}] {event.message}"

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> Group:
        """Build the full dashboard renderable from current state."""
        state = self._state

        # Elapsed time
        elapsed = datetime.now(timezone.utc) - state.start_time
        total_seconds = int(elapsed.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # ---- Status table ----
        status_table = Table(show_header=False, expand=True, box=None)
        status_table.add_column("label", style="bold cyan", ratio=1)
        status_table.add_column("value", ratio=2)
        status_table.add_column("label2", style="bold cyan", ratio=1)
        status_table.add_column("value2", ratio=2)

        # Sprint progress cell
        if state.sprint_current is not None and state.sprint_total is not None:
            sprint_text = Text(f"[{state.sprint_current}/{state.sprint_total}] ")
            progress_bar = ProgressBar(
                total=state.sprint_total,
                completed=state.sprint_current,
                width=20,
            )
            sprint_renderable = Group(sprint_text, progress_bar)
        elif state.sprint_current is not None:
            sprint_renderable = Text(f"Sprint {state.sprint_current}")
        else:
            sprint_renderable = Text("-")

        status_table.add_row(
            "Phase",
            Text(state.current_phase, style="bold white"),
            "Sprint",
            sprint_renderable,
        )
        status_table.add_row(
            "Agent",
            Text(state.active_agent or "-", style="green" if state.active_agent else "dim"),
            "Cost / Mode",
            Text(f"${state.total_cost_usd:.4f}  ({state.mode})"),
        )

        status_panel = Panel(
            status_table,
            title="PIPELINE DASHBOARD",
            subtitle=f"elapsed {elapsed_str}",
            border_style="blue",
        )

        # ---- Event log ----
        if state.recent_events:
            event_lines = "\n".join(state.recent_events)
        else:
            event_lines = "(no events yet)"

        events_panel = Panel(
            Text(event_lines),
            title="Events",
            border_style="dim",
        )

        return Group(status_panel, events_panel)
