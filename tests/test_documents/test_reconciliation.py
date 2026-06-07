"""Tests for cross-document feature/spec reconciliation.

Reconciliation guards against the silent ID-scoping drops that let skillsbloom
ship UAT without testing F004/F005/F006/F008: it checks that the
``F###``/``M###``/``E###`` ID graph across the features doc, sprint plan, and
track specs actually lines up.
"""

from agentic_dev.documents.reconciliation import (
    Finding,
    format_findings,
    has_errors,
    reconcile,
)
from agentic_dev.state.models import SprintState


def _sprint(number, features, tracks):
    """Build a SprintState with a realistic scope_text for *features*."""
    refs = ", ".join(f"[{fid}]" for fid in features)
    scope = (
        f"## Sprint {number}: S{number}\n"
        f"- **Features:** {refs}\n"
        f"- **Tracks in scope:** {', '.join(tracks)}\n"
    )
    return SprintState(
        sprint_number=number,
        name=f"S{number}",
        scope_text=scope,
        tracks_in_scope=list(tracks),
    )


_FEATURES = (
    "# Features Request\n\n"
    "## Feature: [F001] Login\n- [ ] a\n\n"
    "## Feature: [F002] Dashboard\n- [ ] b\n\n"
    "## Feature: [F003] Reports\n- [ ] c\n"
)


def _codes(findings):
    return {f.code for f in findings}


def _by_code(findings, code):
    return [f for f in findings if f.code == code]


class TestReconcileCleanCase:
    def test_consistent_documents_yield_no_findings(self):
        features = (
            "# Features Request\n\n"
            "## Feature: [F001] Login\n- [ ] a\n\n"
            "## Feature: [F002] Dashboard\n- [ ] b\n"
        )
        sprints = [
            _sprint(1, ["F001"], ["frontend"]),
            _sprint(2, ["F002"], ["frontend"]),
        ]
        spec = (
            "# Frontend Spec\n## Pages\n"
            "### [P001] Login\n- **Features:** [F001]\n- x\n"
            "### [P002] Dash\n- **Features:** [F002]\n- y\n"
        )
        findings = reconcile(features, sprints, {"frontend": spec})
        assert findings == []
        assert has_errors(findings) is False


class TestOrphanFeature:
    def test_feature_with_no_sprint_is_orphan_error(self):
        sprints = [
            _sprint(1, ["F001"], ["frontend"]),
            _sprint(2, ["F002"], ["frontend"]),
        ]
        findings = reconcile(_FEATURES, sprints, {})
        orphans = _by_code(findings, "orphan_feature")
        assert len(orphans) == 1
        assert orphans[0].severity == "ERROR"
        assert "F003" in orphans[0].ids
        assert has_errors(findings) is True


class TestDanglingReference:
    def test_sprint_referencing_undefined_feature_is_dangling_error(self):
        features = "# Features Request\n\n## Feature: [F001] Login\n- [ ] a\n"
        sprints = [_sprint(1, ["F001", "F099"], ["frontend"])]
        findings = reconcile(features, sprints, {})
        dangling = _by_code(findings, "dangling_ref")
        assert len(dangling) == 1
        assert dangling[0].severity == "ERROR"
        assert "F099" in dangling[0].ids


class TestNonCanonicalReference:
    def test_prose_only_feature_ref_in_spec_is_flagged(self):
        """The exact skillsbloom F004/F005/F006 case: the spec mentions the
        feature only in parenthesized prose, never bracketed, so scoping and
        UAT-extraction silently miss it."""
        features = (
            "# Features Request\n\n"
            "## Feature: [F001] Login\n- [ ] a\n\n"
            "## Feature: [F002] Goals\n- [ ] b\n"
        )
        sprints = [
            _sprint(1, ["F001"], ["frontend"]),
            _sprint(2, ["F002"], ["frontend"]),
        ]
        spec = (
            "# Frontend Spec\n## Pages\n"
            "### [P001] Login\n- **Features:** [F001]\n- x\n"
            "- Goal modals (F002) — existing\n"  # prose only, never [F002]
        )
        findings = reconcile(features, sprints, {"frontend": spec})
        noncanon = _by_code(findings, "noncanonical_ref")
        assert len(noncanon) == 1
        assert noncanon[0].severity == "WARN"
        assert "F002" in noncanon[0].ids
        assert "frontend" in noncanon[0].message


class TestSpecCoverageGap:
    def test_in_scope_feature_absent_from_spec_is_flagged(self):
        """The F008 case: an in-scope feature the track spec never mentions."""
        features = (
            "# Features Request\n\n"
            "## Feature: [F001] Login\n- [ ] a\n\n"
            "## Feature: [F002] Targets\n- [ ] b\n"
        )
        sprints = [
            _sprint(1, ["F001"], ["frontend"]),
            _sprint(2, ["F002"], ["frontend"]),
        ]
        spec = (
            "# Frontend Spec\n## Pages\n"
            "### [P001] Login\n- **Features:** [F001]\n- x\n"
        )  # F002 absent entirely
        findings = reconcile(features, sprints, {"frontend": spec})
        gaps = _by_code(findings, "spec_coverage_gap")
        assert len(gaps) == 1
        assert gaps[0].severity == "WARN"
        assert "F002" in gaps[0].ids
        assert "frontend" in gaps[0].message

    def test_bracketed_feature_not_flagged_as_gap_or_noncanonical(self):
        features = "# Features Request\n\n## Feature: [F001] Login\n- [ ] a\n"
        sprints = [_sprint(1, ["F001"], ["frontend"])]
        spec = "# Spec\n### [P001] Login\n- **Features:** [F001]\n- x\n"
        findings = reconcile(features, sprints, {"frontend": spec})
        assert _codes(findings) == set()


class TestFormatFindings:
    def test_clean_report(self):
        out = format_findings([])
        assert "No cross-document inconsistencies found." in out

    def test_lists_each_finding_with_severity(self):
        findings = [
            Finding("ERROR", "orphan_feature", "F002 never built", ("F002",)),
            Finding("WARN", "noncanonical_ref", "F004 prose only", ("F004",)),
        ]
        out = format_findings(findings)
        assert "**ERROR** (orphan_feature)" in out
        assert "**WARN** (noncanonical_ref)" in out


class TestHasErrors:
    def test_only_warnings_is_not_errors(self):
        findings = [Finding("WARN", "noncanonical_ref", "msg", ("F002",))]
        assert has_errors(findings) is False

    def test_any_error_is_errors(self):
        findings = [
            Finding("WARN", "noncanonical_ref", "msg", ("F002",)),
            Finding("ERROR", "orphan_feature", "msg", ("F003",)),
        ]
        assert has_errors(findings) is True
