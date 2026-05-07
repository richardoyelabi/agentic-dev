"""Tests for the sync orchestration module."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.orchestrator.sync import (
    SyncApplyResult,
    _collect_design_context,
    _compose_change_request,
    apply_sync_resolutions,
)
from agentic_dev.documents.store import DocumentStore
from agentic_dev.state.models import DriftItem, SyncReport


class TestSyncApplyResult:
    """Tests for the SyncApplyResult dataclass."""

    def test_defaults(self):
        result = SyncApplyResult()
        assert result.specs_updated == 0
        assert result.code_changes_queued == 0
        assert result.items_ignored == 0
        assert result.items_deferred == 0
        assert result.total_cost == 0.0


class TestParseDriftReport:
    """Tests for parsing drift detector agent output via the LLM parser."""

    @staticmethod
    def _parsed(items, summary: str = ""):
        from agentic_dev.state.parser_models import (
            ParsedDriftItem,
            ParsedDriftReport,
        )
        return ParsedDriftReport(
            items=[ParsedDriftItem(**i) for i in items],
            summary=summary,
        )

    async def _call(self, text: str, parsed=None, sanity_raises=False, tmp_path=None):
        from agentic_dev.orchestrator import sync as sync_mod

        claude = MagicMock()
        claude.run = AsyncMock()

        if sanity_raises:
            async def _stub(*_, sanity_check, **__):
                sanity_check(parsed)
            with patch.object(sync_mod, "parse_with_llm", side_effect=_stub):
                return await sync_mod._parse_drift_report(
                    claude=claude, working_dir=tmp_path or Path("/tmp"), text=text,
                )

        with patch.object(sync_mod, "parse_with_llm", AsyncMock(return_value=parsed)):
            return await sync_mod._parse_drift_report(
                claude=claude, working_dir=tmp_path or Path("/tmp"), text=text,
            )

    async def test_parses_basic_report(self):
        text = """# Sync Report

## API Contract
### In code but not in spec
- [DRIFT-001] POST /api/webhooks — found in backend/routes/webhooks.py
- [DRIFT-002] GET /api/preferences — found in backend/routes/prefs.py

### In spec but not in code
- [DRIFT-003] DELETE /api/users/:id — specified in api_contract.md

### Differences
- [DRIFT-004] POST /api/auth response shape differs

## Summary
4 drift items found
"""
        parsed = self._parsed([
            {"id": "[DRIFT-001]", "scope": "api", "category": "in_code_not_spec",
             "description": "POST /api/webhooks", "source_file": "backend/routes/webhooks.py"},
            {"id": "[DRIFT-002]", "scope": "api", "category": "in_code_not_spec",
             "description": "GET /api/preferences", "source_file": "backend/routes/prefs.py"},
            {"id": "[DRIFT-003]", "scope": "api", "category": "in_spec_not_code",
             "description": "DELETE /api/users/:id", "spec_reference": "api_contract.md"},
            {"id": "[DRIFT-004]", "scope": "api", "category": "difference",
             "description": "POST /api/auth response shape differs"},
        ], summary="4 drift items found")

        report = await self._call(text, parsed)
        assert len(report.items) == 4
        assert report.items[0].id == "[DRIFT-001]"
        assert report.items[0].scope == "api"
        assert report.items[0].category == "in_code_not_spec"
        assert report.items[0].source_file == "backend/routes/webhooks.py"
        assert report.items[2].category == "in_spec_not_code"
        assert report.items[2].spec_reference == "api_contract.md"
        assert report.items[3].category == "difference"

    async def test_handles_found_in_inside_description_prose(self):
        """Regression: descriptions that mention 'found in' mid-sentence
        must not be split incorrectly. Under the LLM parser, the model is
        instructed to only treat trailing 'found in <path>' as source_file."""
        text = """# Sync Report

## Backend
### Differences
- [DRIFT-001] Bug found in production logs but fixed differently in code
"""
        parsed = self._parsed([
            {"id": "[DRIFT-001]", "scope": "backend", "category": "difference",
             "description": "Bug found in production logs but fixed differently in code",
             "source_file": None},
        ])
        report = await self._call(text, parsed)
        assert len(report.items) == 1
        assert "production logs" in report.items[0].description
        assert report.items[0].source_file is None

    async def test_empty_report_short_circuits_without_calling_llm(self):
        text = """# Sync Report

## Summary
0 drift items found
"""
        from agentic_dev.orchestrator import sync as sync_mod

        claude = MagicMock()
        claude.run = AsyncMock()
        mock_parse = AsyncMock()

        with patch.object(sync_mod, "parse_with_llm", mock_parse):
            report = await sync_mod._parse_drift_report(
                claude=claude, working_dir=Path("/tmp"), text=text,
            )

        assert len(report.items) == 0
        mock_parse.assert_not_called()

    async def test_count_mismatch_raises_value_error(self):
        text = "## Backend\n### Differences\n- [DRIFT-001] First\n- [DRIFT-002] Second\n"
        # Sanity check expects 2, LLM returns 1 -> sanity fails
        parsed = self._parsed([
            {"id": "[DRIFT-001]", "scope": "backend", "category": "difference",
             "description": "First"},
        ])
        with pytest.raises(ValueError, match="count mismatch"):
            await self._call(text, parsed, sanity_raises=True)

    async def test_summary_falls_back_when_blank(self):
        text = "## Backend\n### Differences\n- [DRIFT-001] One\n"
        parsed = self._parsed([
            {"id": "[DRIFT-001]", "scope": "backend", "category": "difference",
             "description": "One"},
        ], summary="")
        report = await self._call(text, parsed)
        assert "1 drift item" in report.summary

    async def test_all_items_have_no_resolution(self):
        text = """# Sync Report

## Backend
### Differences
- [DRIFT-001] Model field mismatch
- [DRIFT-002] Service return type changed
"""
        parsed = self._parsed([
            {"id": "[DRIFT-001]", "scope": "backend", "category": "difference",
             "description": "Model field mismatch"},
            {"id": "[DRIFT-002]", "scope": "backend", "category": "difference",
             "description": "Service return type changed"},
        ])
        report = await self._call(text, parsed)
        for item in report.items:
            assert item.resolution is None


class TestApplySyncResolutions:
    """Tests for the broadcast spec update behavior."""

    def _make_report(self, items: list[DriftItem]) -> SyncReport:
        return SyncReport(
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

    def _make_doc_store(self, existing_specs: list[str]) -> MagicMock:
        store = MagicMock()
        store.exists.side_effect = lambda name: name in existing_specs
        store.read.return_value = "# Spec content"
        return store

    def _make_qa_cycle_result(self, text: str = "# Updated spec", cost: float = 0.05):
        result = MagicMock()
        result.output = text
        result.total_cost = cost
        return result

    @pytest.fixture
    def mock_deps(self, tmp_path):
        claude = MagicMock()
        claude.run = AsyncMock()
        registry = MagicMock()
        agent_def = MagicMock()
        agent_def.name = "spec_updater"
        agent_def.prompt_template = "spec_updater.md.j2"
        agent_def.constraints = []
        registry.get.return_value = agent_def
        prompt_renderer = MagicMock()
        prompt_renderer.render.return_value = "rendered prompt"
        prompt_renderer.render_agent_prompt = MagicMock(return_value="rendered prompt")
        return claude, registry, prompt_renderer, tmp_path

    @patch("agentic_dev.orchestrator.sync.run_qa_cycle", new_callable=AsyncMock)
    async def test_broadcasts_to_all_existing_specs(self, mock_qa_cycle, mock_deps):
        """to_spec items should be sent to every existing spec, not routed by scope."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["frontend_spec", "backend_spec", "api_contract"])
        mock_qa_cycle.return_value = self._make_qa_cycle_result()

        items = [
            DriftItem(
                id="DRIFT-012",
                scope="api",
                category="in_code_not_spec",
                description="BatchSlideCompletion model — tracks batch-level completions",
                resolution="to_spec",
            ),
        ]
        report = self._make_report(items)

        result = await apply_sync_resolutions(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            report=report,
        )

        assert result.specs_updated == 3
        assert mock_qa_cycle.call_count == 3

    @patch("agentic_dev.orchestrator.sync.run_qa_cycle", new_callable=AsyncMock)
    async def test_skips_nonexistent_specs(self, mock_qa_cycle, mock_deps):
        """Only updates specs that exist in the doc store."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["backend_spec"])
        mock_qa_cycle.return_value = self._make_qa_cycle_result()

        items = [
            DriftItem(
                id="DRIFT-001",
                scope="api",
                category="difference",
                description="Some drift",
                resolution="to_spec",
            ),
        ]
        report = self._make_report(items)

        result = await apply_sync_resolutions(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            report=report,
        )

        assert result.specs_updated == 1
        assert mock_qa_cycle.call_count == 1

    @patch("agentic_dev.orchestrator.sync.run_qa_cycle", new_callable=AsyncMock)
    async def test_no_updater_calls_when_no_to_spec_items(self, mock_qa_cycle, mock_deps):
        """Items without to_spec resolution should not trigger spec updates."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["frontend_spec", "backend_spec", "api_contract"])

        items = [
            DriftItem(
                id="DRIFT-001",
                scope="api",
                category="difference",
                description="Some drift",
                resolution="to_code",
            ),
            DriftItem(
                id="DRIFT-002",
                scope="backend",
                category="difference",
                description="Another drift",
                resolution="ignore",
            ),
        ]
        report = self._make_report(items)

        result = await apply_sync_resolutions(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            report=report,
        )

        assert result.specs_updated == 0
        mock_qa_cycle.assert_not_called()

    @patch("agentic_dev.orchestrator.sync.run_qa_cycle", new_callable=AsyncMock)
    async def test_accumulates_cost_from_all_updaters(self, mock_qa_cycle, mock_deps):
        """Total cost should sum across all spec updater calls."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["frontend_spec", "backend_spec"])
        mock_qa_cycle.side_effect = [
            self._make_qa_cycle_result(cost=0.10),
            self._make_qa_cycle_result(cost=0.15),
        ]

        items = [
            DriftItem(
                id="DRIFT-001",
                scope="api",
                category="difference",
                description="Some drift",
                resolution="to_spec",
            ),
        ]
        report = self._make_report(items)

        result = await apply_sync_resolutions(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            report=report,
        )

        assert result.total_cost == pytest.approx(0.25)


class TestComposeChangeRequest:
    """Tests for composing change request documents."""

    def test_produces_markdown(self):
        items = [
            DriftItem(
                id="DRIFT-001",
                scope="api",
                category="in_spec_not_code",
                description="Missing billing endpoint",
                spec_reference="api_contract.md",
            ),
            DriftItem(
                id="DRIFT-002",
                scope="backend",
                category="in_spec_not_code",
                description="Missing User.avatar field",
            ),
        ]
        result = _compose_change_request(items)
        assert "# Sync Change Request" in result
        assert "DRIFT-001" in result
        assert "DRIFT-002" in result
        assert "Missing billing endpoint" in result
        assert "api_contract.md" in result

    def test_empty_list_produces_header(self):
        result = _compose_change_request([])
        assert "# Sync Change Request" in result


class TestCollectDesignContext:
    """Tests for _collect_design_context helper."""

    @patch("agentic_dev.onboarding.figma.check_figma_mcp_available")
    def test_returns_figma_sources_and_mcp_available(self, mock_check):
        mock_check.return_value = None
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.return_value = "# Figma Sources\n- URL: https://figma.com/file/abc"

        figma_sources, figma_mcp_available = _collect_design_context(doc_store)

        assert "figma.com/file/abc" in figma_sources
        assert figma_mcp_available == "true"

    def test_returns_empty_when_no_figma_sources(self):
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.exists.return_value = False

        figma_sources, figma_mcp_available = _collect_design_context(doc_store)

        assert figma_sources == ""
        assert figma_mcp_available == "false"

    @patch("agentic_dev.onboarding.figma.check_figma_mcp_available")
    def test_mcp_not_configured_returns_false_silently(self, mock_check):
        from agentic_dev.onboarding.figma import FigmaMCPNotConfigured
        mock_check.side_effect = FigmaMCPNotConfigured()
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.return_value = "# Figma Sources\n- URL: https://figma.com/file/abc"

        figma_sources, figma_mcp_available = _collect_design_context(doc_store)

        assert "figma.com/file/abc" in figma_sources
        assert figma_mcp_available == "false"

    @patch("agentic_dev.onboarding.figma.check_figma_mcp_available", side_effect=RuntimeError("auth token expired"))
    def test_unexpected_exception_logs_warning(self, mock_check):
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.return_value = "# Figma Sources\n- URL: https://figma.com/file/abc"

        with patch("agentic_dev.orchestrator.sync.get_event_logger") as mock_logger:
            mock_log = MagicMock()
            mock_logger.return_value = mock_log

            figma_sources, figma_mcp_available = _collect_design_context(doc_store)

        assert "figma.com/file/abc" in figma_sources
        assert figma_mcp_available == "false"
        mock_log.warning.assert_called_once()
        warning_msg = mock_log.warning.call_args[0][0]
        assert "unexpectedly" in warning_msg
