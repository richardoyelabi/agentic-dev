"""Pipeline phase transition validation and execution."""

from datetime import datetime, timezone

from agentic_dev.exceptions import InvalidTransitionError
from agentic_dev.state.models import PipelinePhase, PipelineState

VALID_TRANSITIONS: dict[PipelinePhase, list[PipelinePhase]] = {
    PipelinePhase.IDLE: [PipelinePhase.INPUT_PROCESSING],
    PipelinePhase.INPUT_PROCESSING: [PipelinePhase.FEATURE_ANALYSIS, PipelinePhase.FAILED],
    PipelinePhase.FEATURE_ANALYSIS: [PipelinePhase.FEATURE_ANALYSIS_QA, PipelinePhase.FAILED],
    PipelinePhase.FEATURE_ANALYSIS_QA: [PipelinePhase.ARCHITECTURE, PipelinePhase.FAILED],
    PipelinePhase.ARCHITECTURE: [PipelinePhase.ARCHITECTURE_QA, PipelinePhase.FAILED],
    PipelinePhase.ARCHITECTURE_QA: [PipelinePhase.SPRINT_PLANNING, PipelinePhase.FAILED],
    PipelinePhase.SPRINT_PLANNING: [PipelinePhase.SPRINT_PLANNING_QA, PipelinePhase.FAILED],
    PipelinePhase.SPRINT_PLANNING_QA: [PipelinePhase.DESIGN_CHECKPOINT, PipelinePhase.FAILED],
    PipelinePhase.DESIGN_CHECKPOINT: [PipelinePhase.SPRINTING, PipelinePhase.FAILED],
    PipelinePhase.SPRINTING: [PipelinePhase.UAT, PipelinePhase.FAILED],
    PipelinePhase.UAT: [PipelinePhase.COMPLETE, PipelinePhase.FAILED],
    PipelinePhase.COMPLETE: [],
    PipelinePhase.FAILED: [],
}


def validate_transition(
    from_phase: PipelinePhase, to_phase: PipelinePhase
) -> None:
    """Raise InvalidTransitionError if the transition is not allowed."""
    allowed = VALID_TRANSITIONS.get(from_phase, [])
    if to_phase not in allowed:
        raise InvalidTransitionError(from_phase, to_phase)


def advance_phase(
    state: PipelineState, to_phase: PipelinePhase
) -> PipelineState:
    """Validate the transition and return a new state with the updated phase."""
    validate_transition(state.phase, to_phase)
    state.phase = to_phase
    state.updated_at = datetime.now(timezone.utc)
    return state


def reset_for_update(
    state: PipelineState,
    restart_phase: PipelinePhase,
    mode: str,
) -> PipelineState:
    """Reset a COMPLETE pipeline for an update or remediation cycle.

    Preserves total_cost_usd. Clears sprints, agent_runs, error.
    Sets phase to restart_phase and mode to the given mode.
    If mode is "remediate", increments remediation_cycle.

    Raises InvalidTransitionError if state is not in COMPLETE phase.
    """
    if state.phase != PipelinePhase.COMPLETE:
        raise InvalidTransitionError(state.phase, PipelinePhase.COMPLETE)

    state.phase = restart_phase
    state.mode = mode  # type: ignore[assignment]
    state.sprints = []
    state.agent_runs = []
    state.current_sprint = None
    state.error = None
    state.failed_at_phase = None
    state.checkpoint_feedback = None
    if mode == "remediate":
        state.remediation_cycle += 1
    state.updated_at = datetime.now(timezone.utc)
    return state


def resume_from_failure(state: PipelineState) -> PipelineState:
    """Reset a FAILED state back to the phase where it failed, clearing the error.

    Raises InvalidTransitionError if state is not in FAILED phase or has no
    recorded failed_at_phase.
    """
    if state.phase != PipelinePhase.FAILED:
        raise InvalidTransitionError(state.phase, PipelinePhase.FAILED)

    target_phase = state.failed_at_phase or PipelinePhase.IDLE
    state.phase = target_phase
    state.error = None
    state.failed_at_phase = None
    state.updated_at = datetime.now(timezone.utc)
    return state
