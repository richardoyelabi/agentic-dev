"""Pydantic models for pipeline state management."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from agentic_dev.tracks import Track, TrackProgress


class PipelinePhase(StrEnum):
    """Finite state machine phases for the development pipeline."""

    IDLE = "IDLE"
    INPUT_PROCESSING = "INPUT_PROCESSING"
    INPUT_PROCESSING_QA = "INPUT_PROCESSING_QA"
    FEATURE_ANALYSIS = "FEATURE_ANALYSIS"
    FEATURE_ANALYSIS_QA = "FEATURE_ANALYSIS_QA"
    ARCHITECTURE = "ARCHITECTURE"
    ARCHITECTURE_QA = "ARCHITECTURE_QA"
    SPRINT_PLANNING = "SPRINT_PLANNING"
    SPRINT_PLANNING_QA = "SPRINT_PLANNING_QA"
    DESIGN_CHECKPOINT = "DESIGN_CHECKPOINT"
    SPRINTING = "SPRINTING"
    UAT = "UAT"
    UAT_QA = "UAT_QA"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class SprintStatus(StrEnum):
    """Sprint-wide status, orthogonal to per-track ``TrackProgress``."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    INTEGRATION = "integration"
    INTEGRATION_QA = "integration_qa"
    INTEGRATION_CORRECTION = "integration_correction"
    COMPLETE = "complete"
    FAILED = "failed"


class SprintState(BaseModel):
    """Tracks the state of a single sprint."""

    sprint_number: int
    name: str
    scope_text: str = ""
    status: SprintStatus = SprintStatus.PENDING
    integration_session_id: str | None = None
    integration_services: list[str] = Field(default_factory=list)
    tracks_in_scope: list[str] = Field(default_factory=list)
    track_progress: dict[str, TrackProgress] = Field(default_factory=dict)
    failed_at_step: SprintStatus | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentRunRecord(BaseModel):
    """Records a single agent invocation for cost tracking and auditing."""

    agent_name: str
    phase: str
    sprint: int | None = None
    session_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    cost_usd: float = 0.0
    success: bool = False


class PipelineState(BaseModel):
    """Top-level pipeline state persisted to disk."""

    project_name: str
    phase: PipelinePhase = PipelinePhase.IDLE
    mode: Literal["new", "update", "remediate"] = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sprints: list[SprintState] = Field(default_factory=list)
    current_sprint: int | None = None
    checkpoint_feedback: str | None = None
    # Set when sprint-planning reconciliation finds ERROR-severity cross-document
    # inconsistencies (e.g. a feature scheduled in no sprint). Forces a pause at
    # the design checkpoint even when ``after_design`` is disabled, so guaranteed
    # defects are reviewed before the expensive build; cleared once acknowledged.
    reconciliation_blocked: bool = False
    error: str | None = None
    failed_at_phase: PipelinePhase | None = None
    total_cost_usd: float = 0.0
    remediation_cycle: int = 0
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)
    active_session_id: str | None = None
    # Intra-cycle resume cursor. Only one agent runs at a time, so a single
    # cursor pins the in-flight Claude session and the QA-cycle stage it died at
    # ("action" | "initial_qa" | "correction" | "re_review"), letting the next
    # `agentic-dev resume` continue exactly where it stopped. ``active_session_id``
    # carries the session; ``active_qa_round`` the correction/re-review round.
    active_qa_stage: str | None = None
    active_qa_round: int = 0
    tracks: list[Track] = Field(default_factory=list)
    completed_uat_tracks: list[str] = Field(default_factory=list)
    # Per-track UAT progress: track name -> feature IDs already verified, so a
    # resume skips passed features and re-runs only the rest. Parallel to
    # ``completed_uat_tracks`` (which marks a track fully done).
    completed_uat_features: dict[str, list[str]] = Field(default_factory=dict)
