"""Tests for the adoption orchestration module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.config import DirectoryMap
from agentic_dev.orchestrator.adoption import (
    AdoptionResult,
    _build_structured_input,
    run_adoption,
)
from agentic_dev.state.models import ProjectType


class TestAdoptionResult:
    """Tests for the AdoptionResult dataclass."""

    def test_defaults(self):
        result = AdoptionResult()
        assert result.total_cost == 0.0
        assert result.documents_produced == []
        assert result.features_count == 0
        assert result.endpoints_count == 0

    def test_custom_values(self):
        result = AdoptionResult(
            total_cost=25.50,
            documents_produced=["frontend_spec", "backend_spec"],
            features_count=12,
            endpoints_count=24,
        )
        assert result.total_cost == 25.50
        assert len(result.documents_produced) == 2


class TestBuildStructuredInput:
    """Tests for the _build_structured_input helper."""

    def test_builds_with_features(self, tmp_path):
        from agentic_dev.documents.store import DocumentStore
        (tmp_path / "docs").mkdir()
        doc_store = DocumentStore(tmp_path)

        features_text = (
            "# Features Request\n"
            "## Feature: [EXISTING-F001] User Auth\n"
            "### Description\nHandles login\n"
            "## Feature: [EXISTING-F002] Dashboard\n"
            "### Description\nMain dashboard\n"
        )
        doc_store.write("features", features_text)

        result = _build_structured_input(doc_store, ProjectType.FULLSTACK)

        assert "## Project Type" in result
        assert "fullstack" in result
        assert "[EXISTING-F001]" in result
        assert "[EXISTING-F002]" in result

    def test_builds_without_features(self, tmp_path):
        from agentic_dev.documents.store import DocumentStore
        (tmp_path / "docs").mkdir()
        doc_store = DocumentStore(tmp_path)

        result = _build_structured_input(doc_store, ProjectType.BACKEND_ONLY)

        assert "backend_only" in result
        assert "## Feature Requirements" in result

    def test_includes_tech_stack_from_specs(self, tmp_path):
        from agentic_dev.documents.store import DocumentStore
        (tmp_path / "docs").mkdir()
        doc_store = DocumentStore(tmp_path)

        doc_store.write("backend_spec", (
            "# Backend Spec\n"
            "## Tech Stack\n"
            "- Framework: Django REST Framework\n"
            "- Database: PostgreSQL\n"
        ))

        result = _build_structured_input(doc_store, ProjectType.BACKEND_ONLY)

        assert "- Framework: Django REST Framework" in result
        assert "- Database: PostgreSQL" in result


class TestRunAdoption:
    """Tests for the run_adoption async function."""

    @pytest.mark.asyncio
    async def test_fullstack_produces_all_specs(self, tmp_path):
        from agentic_dev.documents.store import DocumentStore
        from agentic_dev.orchestrator.qa_cycle import QACycleResult

        mock_qa_result = QACycleResult(
            output="# Spec content",
            initial_qa_report="APPROVED",
            final_qa_report="APPROVED",
            action_cost=1.0,
            initial_qa_cost=0.5,
        )

        (tmp_path / "docs").mkdir()
        doc_store = DocumentStore(tmp_path)
        (tmp_path / "client").mkdir()
        (tmp_path / "server").mkdir()

        mock_claude = AsyncMock()
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock()
        mock_renderer = MagicMock()

        directory_map = DirectoryMap(frontend="client", backend="server")

        with patch(
            "agentic_dev.orchestrator.adoption.run_qa_cycle",
            new_callable=AsyncMock,
            return_value=mock_qa_result,
        ):
            result = await run_adoption(
                claude=mock_claude,
                registry=mock_registry,
                prompt_renderer=mock_renderer,
                doc_store=doc_store,
                project_dir=tmp_path,
                directory_map=directory_map,
                project_type=ProjectType.FULLSTACK,
            )

        assert isinstance(result, AdoptionResult)
        assert result.total_cost > 0
        assert "structured_input" in result.documents_produced

    @pytest.mark.asyncio
    async def test_frontend_only_skips_backend(self, tmp_path):
        from agentic_dev.documents.store import DocumentStore
        from agentic_dev.orchestrator.qa_cycle import QACycleResult

        mock_qa_result = QACycleResult(
            output="# Frontend Spec",
            initial_qa_report="APPROVED",
            final_qa_report="APPROVED",
            action_cost=1.0,
            initial_qa_cost=0.5,
        )

        (tmp_path / "docs").mkdir()
        doc_store = DocumentStore(tmp_path)
        (tmp_path / "client").mkdir()

        mock_claude = AsyncMock()
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock()
        mock_renderer = MagicMock()

        directory_map = DirectoryMap(frontend="client")

        with patch(
            "agentic_dev.orchestrator.adoption.run_qa_cycle",
            new_callable=AsyncMock,
            return_value=mock_qa_result,
        ):
            result = await run_adoption(
                claude=mock_claude,
                registry=mock_registry,
                prompt_renderer=mock_renderer,
                doc_store=doc_store,
                project_dir=tmp_path,
                directory_map=directory_map,
                project_type=ProjectType.FRONTEND_ONLY,
            )

        assert "backend_spec" not in result.documents_produced
        assert "api_contract" not in result.documents_produced
