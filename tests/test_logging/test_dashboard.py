"""Tests for the Rich live dashboard module."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console, Group

from agentic_dev.logging.dashboard import DashboardState, PipelineDashboard
from agentic_dev.logging.events import (
    AgentCompleteEvent,
    AgentFailedEvent,
    AgentStartEvent,
    LogEvent,
    PhaseTransitionEvent,
    PipelineStartEvent,
    SprintCompleteEvent,
    SprintFailedEvent,
    SprintStartEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def console() -> Console:
    """Return a Console that captures output instead of printing."""
    return Console(file=MagicMock(), force_terminal=True)


@pytest.fixture()
def dashboard(console: Console) -> PipelineDashboard:
    return PipelineDashboard(console)


# ---------------------------------------------------------------------------
# DashboardState defaults
# ---------------------------------------------------------------------------


class TestDashboardState:
    def test_defaults(self) -> None:
        state = DashboardState()
        assert state.current_phase == "IDLE"
        assert state.active_agent is None
        assert state.sprint_current is None
        assert state.sprint_total is None
        assert state.total_cost_usd == 0.0
        assert state.mode == "new"
        assert isinstance(state.recent_events, deque)
        assert len(state.recent_events) == 0

    def test_recent_events_maxlen(self) -> None:
        state = DashboardState()
        for i in range(60):
            state.recent_events.append(f"event-{i}")
        assert len(state.recent_events) == 50
        assert state.recent_events[0] == "event-10"


# ---------------------------------------------------------------------------
# PipelineDashboard lifecycle
# ---------------------------------------------------------------------------


class TestDashboardLifecycle:
    def test_init_has_no_live(self, dashboard: PipelineDashboard) -> None:
        assert dashboard._live is None

    def test_start_creates_live(self, dashboard: PipelineDashboard) -> None:
        dashboard.start()
        assert dashboard._live is not None
        dashboard.stop()

    def test_stop_clears_live(self, dashboard: PipelineDashboard) -> None:
        dashboard.start()
        dashboard.stop()
        assert dashboard._live is None

    def test_stop_when_not_started_is_noop(
        self, dashboard: PipelineDashboard
    ) -> None:
        dashboard.stop()
        assert dashboard._live is None


# ---------------------------------------------------------------------------
# update() -- state mutations per event type
# ---------------------------------------------------------------------------


class TestDashboardUpdate:
    def test_phase_transition(self, dashboard: PipelineDashboard) -> None:
        event = PhaseTransitionEvent(
            message="Entering DESIGN",
            from_phase="ONBOARDING",
            to_phase="DESIGN",
        )
        dashboard.update(event)
        assert dashboard._state.current_phase == "DESIGN"

    def test_agent_start(self, dashboard: PipelineDashboard) -> None:
        event = AgentStartEvent(
            message="Agent starting",
            agent_name="backend_developer",
            model="opus",
            prompt_length=500,
            working_dir="/tmp",
        )
        dashboard.update(event)
        assert dashboard._state.active_agent == "backend_developer"

    def test_agent_complete_clears_agent_and_adds_cost(
        self, dashboard: PipelineDashboard
    ) -> None:
        dashboard._state.active_agent = "backend_developer"
        dashboard._state.total_cost_usd = 0.10

        event = AgentCompleteEvent(
            message="Agent done",
            agent_name="backend_developer",
            model="opus",
            duration_s=12.5,
            cost_usd=0.25,
            result_length=1000,
        )
        dashboard.update(event)

        assert dashboard._state.active_agent is None
        assert dashboard._state.total_cost_usd == pytest.approx(0.35)

    def test_agent_failed_clears_agent(
        self, dashboard: PipelineDashboard
    ) -> None:
        dashboard._state.active_agent = "frontend_developer"

        event = AgentFailedEvent(
            message="Agent crashed",
            agent_name="frontend_developer",
            model="opus",
            duration_s=5.0,
            exit_code=1,
            error="timeout",
        )
        dashboard.update(event)
        assert dashboard._state.active_agent is None

    def test_sprint_start(self, dashboard: PipelineDashboard) -> None:
        event = SprintStartEvent(
            message="Sprint 2 starting",
            sprint_number=2,
            sprint_name="feature-auth",
            needs_integration=True,
        )
        dashboard.update(event)
        assert dashboard._state.sprint_current == 2

    def test_sprint_complete_clears_sprint(
        self, dashboard: PipelineDashboard
    ) -> None:
        dashboard._state.sprint_current = 3

        event = SprintCompleteEvent(
            message="Sprint 3 done",
            sprint_number=3,
            success=True,
            total_cost=0.50,
            duration_s=120.0,
        )
        dashboard.update(event)
        assert dashboard._state.sprint_current is None

    def test_sprint_failed_clears_sprint(
        self, dashboard: PipelineDashboard
    ) -> None:
        dashboard._state.sprint_current = 1

        event = SprintFailedEvent(
            message="Sprint 1 failed",
            sprint_number=1,
            error="build error",
            partial_cost=0.10,
        )
        dashboard.update(event)
        assert dashboard._state.sprint_current is None

    def test_pipeline_start_sets_mode(
        self, dashboard: PipelineDashboard
    ) -> None:
        event = PipelineStartEvent(
            message="Pipeline starting",
            mode="update",
            phase="ONBOARDING",
            command_args={"spec": "foo"},
        )
        dashboard.update(event)
        assert dashboard._state.mode == "update"

    def test_unknown_event_type_still_appends_to_log(
        self, dashboard: PipelineDashboard
    ) -> None:
        event = LogEvent(
            event_type="something_custom",
            message="hello world",
        )
        dashboard.update(event)
        assert len(dashboard._state.recent_events) == 1
        assert "hello world" in dashboard._state.recent_events[0]


# ---------------------------------------------------------------------------
# _format_event_line
# ---------------------------------------------------------------------------


class TestFormatEventLine:
    def test_format_includes_time_and_message(
        self, dashboard: PipelineDashboard
    ) -> None:
        ts = datetime(2026, 4, 1, 14, 30, 45, tzinfo=timezone.utc)
        event = LogEvent(
            event_type="test",
            message="something happened",
            timestamp=ts,
        )
        line = dashboard._format_event_line(event)
        assert line == "[14:30:45] something happened"


# ---------------------------------------------------------------------------
# _render
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_returns_group(self, dashboard: PipelineDashboard) -> None:
        result = dashboard._render()
        assert isinstance(result, Group)

    def test_render_with_no_events_shows_placeholder(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        result = dashboard._render()
        # Render to string to verify content
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "(no events yet)" in output

    def test_render_with_events(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.recent_events.append("[12:00:00] started")
        dashboard._state.recent_events.append("[12:00:01] progressing")

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "started" in output
        assert "progressing" in output

    def test_render_shows_phase_and_mode(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.current_phase = "DESIGN"
        dashboard._state.mode = "update"

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "DESIGN" in output
        assert "update" in output

    def test_render_shows_active_agent(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.active_agent = "backend_developer"

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "backend_developer" in output

    def test_render_sprint_progress_with_total(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.sprint_current = 2
        dashboard._state.sprint_total = 4

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "[2/4]" in output

    def test_render_sprint_without_total(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.sprint_current = 3
        dashboard._state.sprint_total = None

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "Sprint 3" in output

    def test_render_shows_cost(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        dashboard._state.total_cost_usd = 1.2345

        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "$1.2345" in output

    def test_render_shows_elapsed_time(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "elapsed" in output

    def test_render_shows_dashboard_title(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "PIPELINE DASHBOARD" in output

    def test_render_shows_events_title(
        self, dashboard: PipelineDashboard, console: Console
    ) -> None:
        result = dashboard._render()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "Events" in output


# ---------------------------------------------------------------------------
# Integration: update triggers live refresh
# ---------------------------------------------------------------------------


class TestLiveRefresh:
    def test_update_triggers_live_refresh(
        self, dashboard: PipelineDashboard
    ) -> None:
        """When the Live display is active, update() should call live.update()."""
        dashboard.start()
        try:
            dashboard._live.update = MagicMock()  # type: ignore[union-attr]
            event = LogEvent(event_type="test", message="ping")
            dashboard.update(event)
            dashboard._live.update.assert_called_once()  # type: ignore[union-attr]
        finally:
            dashboard.stop()

    def test_update_without_live_does_not_crash(
        self, dashboard: PipelineDashboard
    ) -> None:
        """update() should work even when the Live display is not running."""
        event = LogEvent(event_type="test", message="ping")
        dashboard.update(event)
        assert len(dashboard._state.recent_events) == 1
