"""Tests for run context and contextvars management."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentic_dev.logging.context import (
    RunContext,
    clear_run_context,
    get_run_context,
    set_run_context,
)


# ---------------------------------------------------------------------------
# Fixture: always clean up the context between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure a clean context before and after each test."""
    token = set_run_context(None)
    yield
    clear_run_context(token)


# ---------------------------------------------------------------------------
# RunContext dataclass
# ---------------------------------------------------------------------------


class TestRunContext:
    def test_fields_set(self) -> None:
        ctx = RunContext(run_id="abc123", project_name="my-proj")
        assert ctx.run_id == "abc123"
        assert ctx.project_name == "my-proj"

    def test_start_time_defaults_to_utc(self) -> None:
        before = datetime.now(timezone.utc)
        ctx = RunContext(run_id="r", project_name="p")
        after = datetime.now(timezone.utc)
        assert before <= ctx.start_time <= after

    def test_sprint_number_defaults_to_none(self) -> None:
        ctx = RunContext(run_id="r", project_name="p")
        assert ctx.sprint_number is None

    def test_sprint_number_is_mutable(self) -> None:
        ctx = RunContext(run_id="r", project_name="p")
        ctx.sprint_number = 3
        assert ctx.sprint_number == 3
        ctx.sprint_number = 5
        assert ctx.sprint_number == 5


# ---------------------------------------------------------------------------
# set / get / clear round-trip
# ---------------------------------------------------------------------------


class TestContextVarOps:
    def test_get_returns_none_when_not_set(self) -> None:
        assert get_run_context() is None

    def test_set_then_get_round_trip(self) -> None:
        ctx = RunContext(run_id="r1", project_name="proj1")
        token = set_run_context(ctx)
        try:
            assert get_run_context() is ctx
        finally:
            clear_run_context(token)

    def test_clear_restores_previous_value(self) -> None:
        ctx_a = RunContext(run_id="a", project_name="pa")
        token_a = set_run_context(ctx_a)

        ctx_b = RunContext(run_id="b", project_name="pb")
        token_b = set_run_context(ctx_b)

        assert get_run_context() is ctx_b
        clear_run_context(token_b)
        assert get_run_context() is ctx_a

        clear_run_context(token_a)

    def test_clear_restores_none_when_no_previous(self) -> None:
        ctx = RunContext(run_id="r", project_name="p")
        token = set_run_context(ctx)
        assert get_run_context() is ctx

        clear_run_context(token)
        assert get_run_context() is None

    def test_multiple_set_clear_cycles(self) -> None:
        """Multiple set/clear cycles should all work correctly."""
        for i in range(5):
            ctx = RunContext(run_id=f"run-{i}", project_name=f"proj-{i}")
            token = set_run_context(ctx)
            assert get_run_context() is ctx
            assert get_run_context().run_id == f"run-{i}"
            clear_run_context(token)
            assert get_run_context() is None
