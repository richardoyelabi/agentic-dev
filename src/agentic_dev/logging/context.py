"""Run context for correlating log events across a pipeline run."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timezone


_run_context: contextvars.ContextVar["RunContext | None"] = contextvars.ContextVar(
    "run_context", default=None
)


@dataclass
class RunContext:
    """Correlation context for the current pipeline run."""

    run_id: str
    project_name: str
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sprint_number: int | None = None


def set_run_context(ctx: RunContext) -> contextvars.Token:
    """Set the current run context. Returns a token for resetting."""
    return _run_context.set(ctx)


def get_run_context() -> RunContext | None:
    """Get the current run context, or None if not set."""
    return _run_context.get()


def clear_run_context(token: contextvars.Token) -> None:
    """Reset the run context to its previous value."""
    _run_context.reset(token)
