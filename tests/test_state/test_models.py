"""Tests for pipeline state models."""

from agentic_dev.state.models import (
    PipelinePhase,
    PipelineState,
    SprintState,
    SprintStatus,
)
from agentic_dev.tracks import Track, TrackPhase, TrackProgress


class TestPipelineState:
    def test_minimal_state(self):
        state = PipelineState(project_name="test")
        assert state.project_name == "test"
        assert state.phase == PipelinePhase.IDLE
        assert state.mode == "new"
        assert state.tracks == []
        assert state.sprints == []
        assert state.total_cost_usd == 0.0
        assert state.remediation_cycle == 0
        assert state.active_session_id is None

    def test_tracks_round_trip(self):
        tracks = [
            Track(name="web", kind="web", uat_kind="web"),
            Track(name="api", kind="api", uat_kind="api"),
        ]
        state = PipelineState(project_name="t", tracks=tracks)
        data = state.model_dump()
        restored = PipelineState.model_validate(data)
        assert restored.tracks == tracks

    def test_active_session_id_round_trip(self):
        state = PipelineState(project_name="t", active_session_id="sess-xyz")
        data = state.model_dump()
        restored = PipelineState.model_validate(data)
        assert restored.active_session_id == "sess-xyz"

    def test_phase_serializes(self):
        state = PipelineState(project_name="t", phase=PipelinePhase.UAT)
        data = state.model_dump()
        assert data["phase"] == "UAT"
        restored = PipelineState.model_validate(data)
        assert restored.phase == PipelinePhase.UAT

    def test_completed_uat_tracks_defaults_to_empty(self):
        state = PipelineState(project_name="test")
        assert state.completed_uat_tracks == []

    def test_completed_uat_tracks_round_trip(self):
        state = PipelineState(project_name="t", completed_uat_tracks=["api", "web"])
        data = state.model_dump()
        restored = PipelineState.model_validate(data)
        assert restored.completed_uat_tracks == ["api", "web"]


class TestSprintState:
    def test_default_track_fields(self):
        sprint = SprintState(sprint_number=1, name="Sprint 1")
        assert sprint.tracks_in_scope == []
        assert sprint.track_progress == {}
        assert sprint.status == SprintStatus.PENDING

    def test_tracks_in_scope_round_trip(self):
        sprint = SprintState(
            sprint_number=1, name="Sprint 1",
            tracks_in_scope=["web", "api"],
            track_progress={
                "web": TrackProgress(track_name="web", phase=TrackPhase.COMPLETE),
            },
        )
        data = sprint.model_dump()
        restored = SprintState.model_validate(data)
        assert restored.tracks_in_scope == ["web", "api"]
        assert restored.track_progress["web"].phase == TrackPhase.COMPLETE


class TestSprintStatus:
    def test_values(self):
        assert SprintStatus.PENDING == "pending"
        assert SprintStatus.IN_PROGRESS == "in_progress"
        assert SprintStatus.INTEGRATION == "integration"
        assert SprintStatus.COMPLETE == "complete"
        assert SprintStatus.FAILED == "failed"
