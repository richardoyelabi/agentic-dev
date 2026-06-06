"""Tests for pipeline phase transition validation."""

from datetime import datetime, timezone

import pytest

from agentic_dev.exceptions import InvalidTransitionError
from agentic_dev.state.models import PipelinePhase, PipelineState, SprintState, SprintStatus
from agentic_dev.state.transitions import (
    VALID_TRANSITIONS,
    advance_phase,
    reset_for_update,
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

    def test_complete_allows_only_defined_transitions(self) -> None:
        allowed = {
            PipelinePhase.INPUT_PROCESSING,
            PipelinePhase.FEATURE_ANALYSIS,
            PipelinePhase.ARCHITECTURE,
        }
        for phase in PipelinePhase:
            if phase in allowed:
                validate_transition(PipelinePhase.COMPLETE, phase)
            else:
                with pytest.raises(InvalidTransitionError):
                    validate_transition(PipelinePhase.COMPLETE, phase)

    def test_failed_allows_resume_transitions(self) -> None:
        from agentic_dev.state.transitions import VALID_TRANSITIONS
        allowed = set(VALID_TRANSITIONS[PipelinePhase.FAILED])
        assert len(allowed) > 0
        for phase in PipelinePhase:
            if phase in allowed:
                validate_transition(PipelinePhase.FAILED, phase)
            else:
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

    def test_clears_active_session_id(self) -> None:
        """A forward transition starts a fresh agent context (no carried session)."""
        state = PipelineState(
            project_name="test-project",
            active_session_id="sess-prev",
        )
        updated = advance_phase(state, PipelinePhase.INPUT_PROCESSING)
        assert updated.active_session_id is None

    def test_clears_qa_cursor_on_forward(self) -> None:
        """A forward transition drops the whole intra-cycle resume cursor."""
        state = PipelineState(
            project_name="test-project",
            active_session_id="sess-prev",
            active_qa_stage="initial_qa",
            active_qa_round=1,
        )
        updated = advance_phase(state, PipelinePhase.INPUT_PROCESSING)
        assert updated.active_qa_stage is None
        assert updated.active_qa_round == 0

    def test_preserves_qa_cursor_on_failed_transition(self) -> None:
        """A swallowed sprint failure routes through advance_phase(FAILED); the
        cursor must survive so the next resume continues that session/stage."""
        state = PipelineState(
            project_name="test-project",
            phase=PipelinePhase.SPRINTING,
            active_session_id="sess-dev",
            active_qa_stage="action",
            active_qa_round=2,
        )
        updated = advance_phase(state, PipelinePhase.FAILED)
        assert updated.active_session_id == "sess-dev"
        assert updated.active_qa_stage == "action"
        assert updated.active_qa_round == 2


class TestResetForUpdate:
    def test_reset_for_update_from_complete(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            total_cost_usd=5.0,
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")

        assert result.phase == PipelinePhase.INPUT_PROCESSING
        assert result.mode == "update"
        assert result.sprints == []
        assert result.agent_runs == []
        assert result.error is None
        assert result.current_sprint is None

    def test_reset_for_update_preserves_cost(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            total_cost_usd=12.50,
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")

        assert result.total_cost_usd == 12.50

    def test_reset_for_remediation_increments_cycle(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            remediation_cycle=0,
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "remediate")

        assert result.mode == "remediate"
        assert result.remediation_cycle == 1

    def test_reset_for_remediation_increments_from_existing(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            remediation_cycle=2,
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "remediate")

        assert result.remediation_cycle == 3

    def test_reset_rejects_non_complete(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.SPRINTING,
        )
        with pytest.raises(InvalidTransitionError):
            reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")

    def test_reset_clears_completed_uat_tracks(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            completed_uat_tracks=["api", "web"],
            completed_uat_features={"web": ["F001"]},
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")
        assert result.completed_uat_tracks == []
        assert result.completed_uat_features == {}

    def test_reset_clears_active_session_id(self) -> None:
        """update/remediate changes inputs, so never resume a stale session."""
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            active_session_id="sess-prev",
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")
        assert result.active_session_id is None

    def test_reset_clears_qa_cursor(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.COMPLETE,
            active_session_id="sess-prev",
            active_qa_stage="correction",
            active_qa_round=1,
        )
        result = reset_for_update(state, PipelinePhase.INPUT_PROCESSING, "update")
        assert result.active_qa_stage is None
        assert result.active_qa_round == 0


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

    def test_raises_when_no_failed_at_phase(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            error="something broke",
        )
        with pytest.raises(InvalidTransitionError):
            resume_from_failure(state)

    def test_raises_when_not_in_failed_state(self) -> None:
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.SPRINTING,
        )
        with pytest.raises(InvalidTransitionError):
            resume_from_failure(state)

    def test_resume_resets_failed_sprint_to_pending_when_no_failed_at_step(self) -> None:
        """FAILED sprint with no failed_at_step falls back to PENDING."""
        failed_sprint = SprintState(
            sprint_number=3,
            name="Overdue Invoice Detection",
            status=SprintStatus.FAILED,
            completed_at=datetime(2026, 4, 4, 16, 0, 48, tzinfo=timezone.utc),
        )
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.SPRINTING,
            sprints=[failed_sprint],
        )
        resumed = resume_from_failure(state)

        assert resumed.sprints[0].status == SprintStatus.PENDING
        assert resumed.sprints[0].completed_at is None

    def test_resume_restores_failed_at_step(self) -> None:
        """FAILED sprint with failed_at_step restores to that sub-step."""
        failed_sprint = SprintState(
            sprint_number=2,
            name="Invoice Ingestion",
            status=SprintStatus.FAILED,
            failed_at_step=SprintStatus.IN_PROGRESS,
            completed_at=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        )
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.SPRINTING,
            sprints=[failed_sprint],
        )
        resumed = resume_from_failure(state)

        assert resumed.sprints[0].status == SprintStatus.IN_PROGRESS
        assert resumed.sprints[0].failed_at_step is None
        assert resumed.sprints[0].completed_at is None

    def test_resume_restores_failed_at_step_integration(self) -> None:
        """FAILED sprint at integration step restores correctly."""
        failed_sprint = SprintState(
            sprint_number=1,
            name="Foundation",
            status=SprintStatus.FAILED,
            failed_at_step=SprintStatus.INTEGRATION,
            completed_at=datetime(2026, 4, 4, 14, 0, 0, tzinfo=timezone.utc),
        )
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.SPRINTING,
            sprints=[failed_sprint],
        )
        resumed = resume_from_failure(state)

        assert resumed.sprints[0].status == SprintStatus.INTEGRATION
        assert resumed.sprints[0].failed_at_step is None

    def test_resume_preserves_completed_uat_tracks(self) -> None:
        """Tracks that passed UAT before the failure must not be re-run on resume."""
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.UAT,
            error="uat_web agent raised AgentRunError",
            completed_uat_tracks=["api"],
            completed_uat_features={"web": ["F001", "F002"]},
        )
        resumed = resume_from_failure(state)

        assert resumed.completed_uat_tracks == ["api"]
        assert resumed.completed_uat_features == {"web": ["F001", "F002"]}
        assert resumed.phase == PipelinePhase.UAT
        assert resumed.error is None

    def test_resume_preserves_active_session_id(self) -> None:
        """A plain resume continues the failed agent's Claude session (--resume)."""
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.UAT,
            active_session_id="sess-uat-web",
        )
        resumed = resume_from_failure(state)
        assert resumed.active_session_id == "sess-uat-web"

    def test_resume_preserves_qa_cursor(self) -> None:
        """Resume continues the exact stage/round the cycle died at."""
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.SPRINTING,
            active_session_id="sess-dev",
            active_qa_stage="correction",
            active_qa_round=1,
        )
        resumed = resume_from_failure(state)
        assert resumed.active_qa_stage == "correction"
        assert resumed.active_qa_round == 1

    def test_resume_preserves_complete_sprints(self) -> None:
        complete_sprint = SprintState(
            sprint_number=1,
            name="Foundation",
            status=SprintStatus.COMPLETE,
            completed_at=datetime(2026, 4, 4, 10, 0, 0, tzinfo=timezone.utc),
        )
        failed_sprint = SprintState(
            sprint_number=2,
            name="Invoice Ingestion",
            status=SprintStatus.FAILED,
            failed_at_step=SprintStatus.IN_PROGRESS,
            completed_at=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        )
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.FAILED,
            failed_at_phase=PipelinePhase.SPRINTING,
            sprints=[complete_sprint, failed_sprint],
        )
        resumed = resume_from_failure(state)

        assert resumed.sprints[0].status == SprintStatus.COMPLETE
        assert resumed.sprints[0].completed_at is not None
        assert resumed.sprints[1].status == SprintStatus.IN_PROGRESS
        assert resumed.sprints[1].completed_at is None
        assert resumed.sprints[1].failed_at_step is None
