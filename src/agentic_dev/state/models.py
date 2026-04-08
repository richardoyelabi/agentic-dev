"""Pydantic models for pipeline state management."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ProjectType(StrEnum):
    """Type of project being developed."""

    FULLSTACK = "fullstack"
    FRONTEND_ONLY = "frontend_only"
    BACKEND_ONLY = "backend_only"


class PipelinePhase(StrEnum):
    """Finite state machine phases for the development pipeline."""

    IDLE = "IDLE"
    INPUT_PROCESSING = "INPUT_PROCESSING"
    FEATURE_ANALYSIS = "FEATURE_ANALYSIS"
    FEATURE_ANALYSIS_QA = "FEATURE_ANALYSIS_QA"
    ARCHITECTURE = "ARCHITECTURE"
    ARCHITECTURE_QA = "ARCHITECTURE_QA"
    SPRINT_PLANNING = "SPRINT_PLANNING"
    SPRINT_PLANNING_QA = "SPRINT_PLANNING_QA"
    DESIGN_CHECKPOINT = "DESIGN_CHECKPOINT"
    SPRINTING = "SPRINTING"
    UAT = "UAT"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ADOPTING = "ADOPTING"
    SYNCING = "SYNCING"
    ADOPTED = "ADOPTED"


class SprintStatus(StrEnum):
    """Status values for an individual sprint."""

    PENDING = "pending"
    BACKEND_DEV = "backend_dev"
    BACKEND_QA = "backend_qa"
    BACKEND_CORRECTION = "backend_correction"
    FRONTEND_DEV = "frontend_dev"
    FRONTEND_QA = "frontend_qa"
    FRONTEND_CORRECTION = "frontend_correction"
    INTEGRATION = "integration"
    INTEGRATION_QA = "integration_qa"
    INTEGRATION_CORRECTION = "integration_correction"
    COMPLETE = "complete"
    FAILED = "failed"


class SprintState(BaseModel):
    """Tracks the state of a single sprint."""

    sprint_number: int
    name: str
    status: SprintStatus = SprintStatus.PENDING
    backend_session_id: str | None = None
    frontend_session_id: str | None = None
    integration_session_id: str | None = None
    integration_services: list[str] = Field(default_factory=list)
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
    project_type: ProjectType | None = None
    phase: PipelinePhase = PipelinePhase.IDLE
    mode: Literal["new", "update", "remediate", "adopt"] = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sprints: list[SprintState] = Field(default_factory=list)
    current_sprint: int | None = None
    checkpoint_feedback: str | None = None
    error: str | None = None
    failed_at_phase: PipelinePhase | None = None
    total_cost_usd: float = 0.0
    remediation_cycle: int = 0
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)
    active_session_id: str | None = None
    origin: Literal["created", "adopted"] = "created"
    last_sync_at: datetime | None = None

    @property
    def has_frontend(self) -> bool:
        """Whether this project includes a frontend."""
        return self.project_type in (
            None, ProjectType.FULLSTACK, ProjectType.FRONTEND_ONLY
        )

    @property
    def has_backend(self) -> bool:
        """Whether this project includes a backend."""
        return self.project_type in (
            None, ProjectType.FULLSTACK, ProjectType.BACKEND_ONLY
        )

    @property
    def expected_architecture_docs(self) -> list[str]:
        """Architecture documents expected based on project type."""
        if self.project_type == ProjectType.FRONTEND_ONLY:
            return ["frontend_spec"]
        if self.project_type == ProjectType.BACKEND_ONLY:
            return ["backend_spec", "api_contract"]
        return ["frontend_spec", "backend_spec", "api_contract"]


class DriftItem(BaseModel):
    """A single item of drift detected between code, specs, and/or Figma designs."""

    id: str
    scope: Literal["api", "frontend", "backend", "figma"]
    category: Literal[
        "in_code_not_spec", "in_spec_not_code", "difference", "design_drift"
    ]
    description: str
    source_file: str | None = None
    spec_reference: str | None = None
    resolution: Literal["to_spec", "to_code", "ignore", "defer"] | None = None


class SyncReport(BaseModel):
    """Structured report of drift between code, specs, and Figma designs."""

    generated_at: datetime
    scope: Literal["all", "api", "frontend", "backend"] = "all"
    items: list[DriftItem] = Field(default_factory=list)
    summary: str = ""
