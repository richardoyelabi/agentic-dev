"""Tests for pipeline phase transition validation."""

from datetime import datetime, timezone

import pytest

from agentic_dev.exceptions import InvalidTransitionError
from agentic_dev.state.models import PipelinePhase, PipelineState
from agentic_dev.state.transitions import (
    VALID_TRANSITIONS,
    advance_phase,
    resume_from_failure,
    validate_transition,
)


class TestValidateTransition:
    @pytest.mark.parametrize(
        "from_phase,to_phase",
        [
            (from_p, to_p)
            for from_p, targets in VALID_TRANSITIONS.items()
            for to_p in targets
        ],
    )
    def test_all_valid_transitions_succeed(
        self, from_phase: PipelinePhase, to_phase: PipelinePhase
    ) -> None:
        # Should not raise
        validate_transition(from_phase, to_phase)

    def test_invalid_transition_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(PipelinePhase.IDLE, PipelinePhase.COMPLETE)

    def test_transition_from_complete_always_invalid(self) -> None:
        for phase in PipelinePhase:
            with pytest.raises(InvalidTransitionError):
                validate_transition(PipelinePhase.COMPLETE, phase)

    def test_transition_from_failed_always_invalid(self) -> None:
        for phase in PipelinePhase:
            with pytest.raises(InvalidTransitionError):
                validate_transition(PipelinePhase.FAILED, phase)


class TestAdvancePhase:
    def test_updates_phase_and_timestamp(self) -> None:
        before = datetime.now(timezone.utc)
        state = PipelineState(project_name="test-project")
        assert state.phase == PipelinePhase.IDLE

        updated = advance_phase(state, PipelinePhase.INPUT_PROCESSING)

        assert updated.phase == PipelinePhase.INPUT_PROCESSING
        assert updated.updated_at >= before

    def test_rejects_invalid_advance(self) -> None:
        state = PipelineState(project_name="test-project")
        with pytest.raises(InvalidTransitionError):
            advance_phase(state, PipelinePhase.UAT)


class TestResumeFromFailure:
    def test_resumes_to_failed_at_phase(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.ARCHITECTURE,
            error="something broke",
        )
        resumed = resume_from_failure(state)

        assert resumed.phase == PipelinePhase.ARCHITECTURE
        assert resumed.error is None
        assert resumed.failed_at_phase is None

    def test_resumes_to_idle_when_no_failed_at_phase(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            error="something broke",
        )
        resumed = resume_from_failure(state)

        assert resumed.phase == PipelinePhase.IDLE

    def test_raises_when_not_in_failed_state(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.SPRINTING,
        )
        with pytest.raises(InvalidTransitionError):
            resume_from_failure(state)
