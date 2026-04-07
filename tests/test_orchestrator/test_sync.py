"""Tests for the sync orchestration module."""

import pytest

from agentic_dev.orchestrator.sync import (
    SyncApplyResult,
    _compose_change_request,
    _group_items_by_spec,
    _parse_drift_report,
)
from agentic_dev.state.models import DriftItem


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


class TestGroupItemsBySpec:
    """Tests for grouping drift items by spec document."""

    def test_groups_by_scope(self):
        items = [
            DriftItem(id="DRIFT-001", scope="api", category="difference", description="x"),
            DriftItem(id="DRIFT-002", scope="frontend", category="in_code_not_spec", description="y"),
            DriftItem(id="DRIFT-003", scope="api", category="in_spec_not_code", description="z"),
            DriftItem(id="DRIFT-004", scope="backend", category="difference", description="w"),
        ]
        groups = _group_items_by_spec(items)
        assert len(groups["api_contract"]) == 2
        assert len(groups["frontend_spec"]) == 1
        assert len(groups["backend_spec"]) == 1


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
