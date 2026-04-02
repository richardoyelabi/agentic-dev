"""Tests for pipeline state models, including ProjectType."""

import pytest

from agentic_dev.state.models import PipelineState, ProjectType


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
