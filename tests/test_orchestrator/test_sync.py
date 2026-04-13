"""Tests for the sync orchestration module."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.orchestrator.sync import (
    SyncApplyResult,
    _collect_design_context,
    _compose_change_request,
    _parse_drift_report,
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
    """Tests for parsing drift detector agent output."""

    def test_parses_basic_report(self):
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
        report = _parse_drift_report(text)
        assert len(report.items) == 4
        assert report.items[0].id == "[DRIFT-001]"
        assert report.items[0].scope == "api"
        assert report.items[0].category == "in_code_not_spec"
        assert report.items[0].source_file == "backend/routes/webhooks.py"
        assert report.items[2].category == "in_spec_not_code"
        assert report.items[2].spec_reference == "api_contract.md"
        assert report.items[3].category == "difference"

    def test_parses_frontend_section(self):
        text = """# Sync Report

## Frontend
### In code but not in spec
- [DRIFT-001] SettingsPage component — found in src/pages/Settings.tsx

## Summary
1 drift item found
"""
        report = _parse_drift_report(text)
        assert len(report.items) == 1
        assert report.items[0].scope == "frontend"
        assert report.items[0].source_file == "src/pages/Settings.tsx"

    def test_parses_figma_section(self):
        text = """# Sync Report

## Figma vs Spec
### Design token drift
- [DRIFT-001] Primary color: Figma #3B82F6, spec #2563EB

## Summary
1 drift item found
"""
        report = _parse_drift_report(text)
        assert len(report.items) == 1
        assert report.items[0].scope == "figma"
        assert report.items[0].category == "design_drift"

    def test_empty_report(self):
        text = """# Sync Report

## Summary
0 drift items found
"""
        report = _parse_drift_report(text)
        assert len(report.items) == 0
        assert "0" in report.summary

    def test_summary_extracted(self):
        text = """# Sync Report

## API Contract
### In code but not in spec
- [DRIFT-001] Something

## Summary
1 item found, 1 in API Contract
"""
        report = _parse_drift_report(text)
        assert "1 item found" in report.summary

    def test_all_items_have_no_resolution(self):
        text = """# Sync Report

## Backend
### Differences
- [DRIFT-001] Model field mismatch
- [DRIFT-002] Service return type changed
"""
        report = _parse_drift_report(text)
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

    def _make_claude_result(self, text: str = "# Updated spec", cost: float = 0.05):
        result = MagicMock()
        result.text = text
        result.cost_usd = cost
        return result

    @pytest.fixture
    def mock_deps(self, tmp_path):
        claude = MagicMock()
        claude.run = AsyncMock()
        registry = MagicMock()
        agent_def = MagicMock()
        agent_def.prompt_template = "spec_updater.md.j2"
        agent_def.constraints = []
        registry.get.return_value = agent_def
        prompt_renderer = MagicMock()
        prompt_renderer.render.return_value = "rendered prompt"
        return claude, registry, prompt_renderer, tmp_path

    async def test_broadcasts_to_all_existing_specs(self, mock_deps):
        """to_spec items should be sent to every existing spec, not routed by scope."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["frontend_spec", "backend_spec", "api_contract"])
        claude.run.return_value = self._make_claude_result()

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
        assert claude.run.call_count == 3

    async def test_skips_nonexistent_specs(self, mock_deps):
        """Only updates specs that exist in the doc store."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["backend_spec"])
        claude.run.return_value = self._make_claude_result()

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
        assert claude.run.call_count == 1

    async def test_no_updater_calls_when_no_to_spec_items(self, mock_deps):
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
        claude.run.assert_not_called()

    async def test_accumulates_cost_from_all_updaters(self, mock_deps):
        """Total cost should sum across all spec updater calls."""
        claude, registry, prompt_renderer, project_dir = mock_deps
        doc_store = self._make_doc_store(["frontend_spec", "backend_spec"])
        claude.run.side_effect = [
            self._make_claude_result(cost=0.10),
            self._make_claude_result(cost=0.15),
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

    @patch("agentic_dev.onboarding.figma.check_figma_mcp_available", side_effect=Exception("not configured"))
    def test_mcp_unavailable_returns_false(self, mock_check):
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.return_value = "# Figma Sources\n- URL: https://figma.com/file/abc"

        figma_sources, figma_mcp_available = _collect_design_context(doc_store)

        assert "figma.com/file/abc" in figma_sources
        assert figma_mcp_available == "false"
