"""Tests for pipeline state models, including ProjectType."""

import pytest

from agentic_dev.state.models import (
    DriftItem,
    PipelinePhase,
    PipelineState,
    ProjectType,
    SprintState,
    SprintStatus,
    SyncReport,
)


class TestProjectType:
    """Tests for the ProjectType enum."""

    def test_enum_values(self):
        assert ProjectType.FULLSTACK == "fullstack"
        assert ProjectType.FRONTEND_ONLY == "frontend_only"
        assert ProjectType.BACKEND_ONLY == "backend_only"

    def test_from_string(self):
        assert ProjectType("fullstack") == ProjectType.FULLSTACK
        assert ProjectType("frontend_only") == ProjectType.FRONTEND_ONLY
        assert ProjectType("backend_only") == ProjectType.BACKEND_ONLY

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ProjectType("invalid")


class TestPipelineStateProjectType:
    """Tests for project_type field on PipelineState."""

    def test_project_type_defaults_to_none(self):
        state = PipelineState(project_name="test")
        assert state.project_type is None

    def test_project_type_set_explicitly(self):
        state = PipelineState(
            project_name="test",
            project_type=ProjectType.FRONTEND_ONLY,
        )
        assert state.project_type == ProjectType.FRONTEND_ONLY

    def test_project_type_serializes_to_json(self):
        state = PipelineState(
            project_name="test",
            project_type=ProjectType.BACKEND_ONLY,
        )
        data = state.model_dump()
        assert data["project_type"] == "backend_only"

    def test_project_type_deserializes_from_json(self):
        data = {"project_name": "test", "project_type": "frontend_only"}
        state = PipelineState.model_validate(data)
        assert state.project_type == ProjectType.FRONTEND_ONLY

    def test_project_type_none_in_json(self):
        data = {"project_name": "test"}
        state = PipelineState.model_validate(data)
        assert state.project_type is None


class TestPipelineStateHelpers:
    """Tests for has_frontend, has_backend, expected_architecture_docs properties."""

    def test_has_frontend_fullstack(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FULLSTACK
        )
        assert state.has_frontend is True

    def test_has_frontend_frontend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FRONTEND_ONLY
        )
        assert state.has_frontend is True

    def test_has_frontend_backend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.BACKEND_ONLY
        )
        assert state.has_frontend is False

    def test_has_backend_fullstack(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FULLSTACK
        )
        assert state.has_backend is True

    def test_has_backend_frontend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FRONTEND_ONLY
        )
        assert state.has_backend is False

    def test_has_backend_backend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.BACKEND_ONLY
        )
        assert state.has_backend is True

    def test_expected_architecture_docs_fullstack(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FULLSTACK
        )
        assert state.expected_architecture_docs == [
            "frontend_spec", "backend_spec", "api_contract"
        ]

    def test_expected_architecture_docs_frontend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.FRONTEND_ONLY
        )
        assert state.expected_architecture_docs == ["frontend_spec"]

    def test_expected_architecture_docs_backend_only(self):
        state = PipelineState(
            project_name="test", project_type=ProjectType.BACKEND_ONLY
        )
        assert state.expected_architecture_docs == ["backend_spec", "api_contract"]

    def test_has_frontend_none_project_type(self):
        state = PipelineState(project_name="test")
        assert state.has_frontend is True

    def test_has_backend_none_project_type(self):
        state = PipelineState(project_name="test")
        assert state.has_backend is True

    def test_expected_architecture_docs_none_project_type(self):
        state = PipelineState(project_name="test")
        assert state.expected_architecture_docs == [
            "frontend_spec", "backend_spec", "api_contract"
        ]


class TestSprintStateNewFields:
    """Tests for new crash-resilience fields on SprintState."""

    def test_integration_session_id_defaults_to_none(self):
        sprint = SprintState(sprint_number=1, name="Sprint 1")
        assert sprint.integration_session_id is None

    def test_failed_at_step_defaults_to_none(self):
        sprint = SprintState(sprint_number=1, name="Sprint 1")
        assert sprint.failed_at_step is None

    def test_integration_session_id_set_explicitly(self):
        sprint = SprintState(
            sprint_number=1,
            name="Sprint 1",
            integration_session_id="sess-123",
        )
        assert sprint.integration_session_id == "sess-123"

    def test_failed_at_step_set_explicitly(self):
        sprint = SprintState(
            sprint_number=1,
            name="Sprint 1",
            failed_at_step=SprintStatus.FRONTEND_DEV,
        )
        assert sprint.failed_at_step == SprintStatus.FRONTEND_DEV

    def test_sprint_state_serializes_new_fields(self):
        sprint = SprintState(
            sprint_number=1,
            name="Sprint 1",
            integration_session_id="sess-456",
            failed_at_step=SprintStatus.BACKEND_DEV,
        )
        data = sprint.model_dump()
        assert data["integration_session_id"] == "sess-456"
        assert data["failed_at_step"] == "backend_dev"

    def test_sprint_state_deserializes_new_fields(self):
        data = {
            "sprint_number": 1,
            "name": "Sprint 1",
            "integration_session_id": "sess-789",
            "failed_at_step": "frontend_dev",
        }
        sprint = SprintState.model_validate(data)
        assert sprint.integration_session_id == "sess-789"
        assert sprint.failed_at_step == SprintStatus.FRONTEND_DEV

    def test_sprint_state_backward_compat_missing_new_fields(self):
        data = {"sprint_number": 1, "name": "Sprint 1", "status": "pending"}
        sprint = SprintState.model_validate(data)
        assert sprint.integration_session_id is None
        assert sprint.failed_at_step is None


class TestPipelineStateNewFields:
    """Tests for new crash-resilience fields on PipelineState."""

    def test_active_session_id_defaults_to_none(self):
        state = PipelineState(project_name="test")
        assert state.active_session_id is None

    def test_active_session_id_set_explicitly(self):
        state = PipelineState(
            project_name="test",
            active_session_id="sess-abc",
        )
        assert state.active_session_id == "sess-abc"

    def test_active_session_id_serializes(self):
        state = PipelineState(
            project_name="test",
            active_session_id="sess-def",
        )
        data = state.model_dump()
        assert data["active_session_id"] == "sess-def"

    def test_active_session_id_deserializes(self):
        data = {"project_name": "test", "active_session_id": "sess-ghi"}
        state = PipelineState.model_validate(data)
        assert state.active_session_id == "sess-ghi"

    def test_pipeline_state_backward_compat_missing_active_session_id(self):
        data = {"project_name": "test"}
        state = PipelineState.model_validate(data)
        assert state.active_session_id is None


class TestAdoptSyncStateFields:
    """Tests for adopt/sync fields on PipelinePhase and PipelineState."""

    def test_new_pipeline_phases_exist(self):
        assert PipelinePhase.ADOPTING == "ADOPTING"
        assert PipelinePhase.SYNCING == "SYNCING"
        assert PipelinePhase.ADOPTED == "ADOPTED"

    def test_mode_accepts_adopt(self):
        state = PipelineState(project_name="test", mode="adopt")
        assert state.mode == "adopt"

    def test_origin_defaults_to_created(self):
        state = PipelineState(project_name="test")
        assert state.origin == "created"

    def test_origin_set_to_adopted(self):
        state = PipelineState(project_name="test", origin="adopted")
        assert state.origin == "adopted"

    def test_last_sync_at_defaults_to_none(self):
        state = PipelineState(project_name="test")
        assert state.last_sync_at is None

    def test_last_sync_at_set_explicitly(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        state = PipelineState(project_name="test", last_sync_at=now)
        assert state.last_sync_at == now

    def test_backward_compat_missing_new_fields(self):
        data = {"project_name": "test", "mode": "new"}
        state = PipelineState.model_validate(data)
        assert state.origin == "created"
        assert state.last_sync_at is None

    def test_adopted_phase_serializes(self):
        state = PipelineState(
            project_name="test",
            phase=PipelinePhase.ADOPTED,
            origin="adopted",
        )
        data = state.model_dump()
        assert data["phase"] == "ADOPTED"
        assert data["origin"] == "adopted"
        restored = PipelineState.model_validate(data)
        assert restored.phase == PipelinePhase.ADOPTED


class TestDriftItemAndSyncReport:
    """Tests for DriftItem and SyncReport models."""

    def test_drift_item_creation(self):
        item = DriftItem(
            id="DRIFT-001",
            scope="api",
            category="in_code_not_spec",
            description="POST /api/webhooks found in code",
            source_file="backend/routes/webhooks.py",
        )
        assert item.id == "DRIFT-001"
        assert item.resolution is None

    def test_drift_item_with_resolution(self):
        item = DriftItem(
            id="DRIFT-002",
            scope="frontend",
            category="difference",
            description="Component mismatch",
            resolution="to_spec",
        )
        assert item.resolution == "to_spec"

    def test_sync_report_creation(self):
        from datetime import datetime, timezone
        report = SyncReport(
            generated_at=datetime.now(timezone.utc),
            scope="api",
            items=[
                DriftItem(
                    id="DRIFT-001",
                    scope="api",
                    category="in_code_not_spec",
                    description="New endpoint found",
                ),
            ],
            summary="1 drift item found",
        )
        assert len(report.items) == 1
        assert report.scope == "api"

    def test_sync_report_defaults(self):
        from datetime import datetime, timezone
        report = SyncReport(generated_at=datetime.now(timezone.utc))
        assert report.scope == "all"
        assert report.items == []
        assert report.summary == ""

    def test_sync_report_serialization_roundtrip(self):
        from datetime import datetime, timezone
        report = SyncReport(
            generated_at=datetime.now(timezone.utc),
            items=[
                DriftItem(
                    id="DRIFT-001",
                    scope="backend",
                    category="in_spec_not_code",
                    description="Missing endpoint",
                    spec_reference="api_contract.md",
                    resolution="to_code",
                ),
            ],
            summary="1 item",
        )
        data = report.model_dump()
        restored = SyncReport.model_validate(data)
        assert restored.items[0].id == "DRIFT-001"
        assert restored.items[0].resolution == "to_code"
