"""Structured log event models for the agentic-dev pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


class LogEvent(BaseModel):
    """Base model for all structured log events."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    run_id: str = ""
    project_name: str = ""
    level: str = "INFO"
    message: str


# ---------------------------------------------------------------------------
# Pipeline lifecycle
# ---------------------------------------------------------------------------


class PipelineStartEvent(LogEvent):
    """Emitted when the pipeline begins execution."""

    event_type: str = "pipeline_start"
    mode: str
    phase: str
    command_args: dict[str, object]


class PipelineCompleteEvent(LogEvent):
    """Emitted when the pipeline finishes successfully."""

    event_type: str = "pipeline_complete"
    total_cost_usd: float
    total_duration_s: float
    sprint_count: int


class PipelineFailedEvent(LogEvent):
    """Emitted when the pipeline fails."""

    event_type: str = "pipeline_failed"
    error: str
    failed_at_phase: str
    traceback: str = ""


class PipelineCheckpointEvent(LogEvent):
    """Emitted at a pipeline checkpoint."""

    event_type: str = "pipeline_checkpoint"
    phase: str
    total_cost_usd: float
    documents_produced: list[str]


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------


class PhaseTransitionEvent(LogEvent):
    """Emitted when the pipeline transitions between phases."""

    event_type: str = "phase_transition"
    from_phase: str
    to_phase: str


# ---------------------------------------------------------------------------
# Agent invocations
# ---------------------------------------------------------------------------


class AgentStartEvent(LogEvent):
    """Emitted when an agent begins execution."""

    event_type: str = "agent_start"
    agent_name: str
    model: str
    prompt_length: int
    working_dir: str
    sprint: int | None = None


class AgentCompleteEvent(LogEvent):
    """Emitted when an agent finishes successfully."""

    event_type: str = "agent_complete"
    agent_name: str
    model: str
    duration_s: float
    cost_usd: float
    result_length: int
    session_id: str | None = None
    sprint: int | None = None


class AgentFailedEvent(LogEvent):
    """Emitted when an agent fails."""

    event_type: str = "agent_failed"
    agent_name: str
    model: str
    duration_s: float
    exit_code: int
    error: str
    sprint: int | None = None


class AgentRetryEvent(LogEvent):
    """Emitted when an agent invocation is retried due to rate limiting."""

    event_type: str = "agent_retry"
    agent_name: str
    model: str
    attempt: int
    max_retries: int
    wait_seconds: float
    wait_source: str
    reason: str
    will_resume_session: bool
    sprint: int | None = None


# ---------------------------------------------------------------------------
# QA cycle
# ---------------------------------------------------------------------------


class QACycleStartEvent(LogEvent):
    """Emitted when a QA cycle begins."""

    event_type: str = "qa_cycle_start"
    action_agent: str
    qa_agent: str
    output_doc_name: str
    sprint: int | None = None


class QACycleVerdictEvent(LogEvent):
    """Emitted when the QA agent delivers a verdict."""

    event_type: str = "qa_cycle_verdict"
    action_agent: str
    qa_agent: str
    issues_found: bool
    sprint: int | None = None


class QACycleCorrectionEvent(LogEvent):
    """Emitted when a correction pass is triggered."""

    event_type: str = "qa_cycle_correction"
    action_agent: str
    correction_cost: float
    sprint: int | None = None


class QACycleCompleteEvent(LogEvent):
    """Emitted when a QA cycle finishes."""

    event_type: str = "qa_cycle_complete"
    action_agent: str
    qa_agent: str
    corrected: bool
    total_cost: float
    sprint: int | None = None


# ---------------------------------------------------------------------------
# Sprint
# ---------------------------------------------------------------------------


class SprintStartEvent(LogEvent):
    """Emitted when a sprint begins."""

    event_type: str = "sprint_start"
    sprint_number: int
    sprint_name: str
    needs_integration: bool


class SprintPhaseEvent(LogEvent):
    """Emitted when a sprint enters a sub-phase."""

    event_type: str = "sprint_phase"
    sprint_number: int
    sub_phase: str


class SprintCompleteEvent(LogEvent):
    """Emitted when a sprint finishes successfully."""

    event_type: str = "sprint_complete"
    sprint_number: int
    success: bool
    total_cost: float
    duration_s: float


class SprintFailedEvent(LogEvent):
    """Emitted when a sprint fails."""

    event_type: str = "sprint_failed"
    sprint_number: int
    error: str
    partial_cost: float


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class DocumentWriteEvent(LogEvent):
    """Emitted when a document is written."""

    event_type: str = "document_write"
    doc_name: str
    content_length: int
    path: str


class DocumentReadEvent(LogEvent):
    """Emitted when a document is read."""

    event_type: str = "document_read"
    doc_name: str
    content_length: int
    path: str


class DocumentArchiveEvent(LogEvent):
    """Emitted when documents are archived."""

    event_type: str = "document_archive"
    cycle_label: str
    archive_path: str


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class StateSaveEvent(LogEvent):
    """Emitted when pipeline state is saved."""

    event_type: str = "state_save"
    phase: str
    total_cost_usd: float
    sprint_count: int


class StateLoadEvent(LogEvent):
    """Emitted when pipeline state is loaded."""

    event_type: str = "state_load"
    phase: str
    total_cost_usd: float


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


class PromptRenderedEvent(LogEvent):
    """Emitted when a prompt template is rendered."""

    event_type: str = "prompt_rendered"
    template_name: str
    context_keys: list[str]
    output_length: int
    correction_mode: bool = False


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


class CheckpointDecisionEvent(LogEvent):
    """Emitted when a checkpoint decision is made."""

    event_type: str = "checkpoint_decision"
    phase: str
    should_pause: bool
    config_snapshot: dict[str, object]
