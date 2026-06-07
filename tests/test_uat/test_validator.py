"""Tests for the UAT report validator (false-PASS structural gate)."""


from agentic_dev.uat.validator import validate_uat_report


def _report(
    *,
    overall: str = "PASS",
    acs: list[dict] | None = None,
    extra_top: str = "",
) -> str:
    """Build a minimal UAT report. Each AC is a dict with result/mode/driver/evidence/artifacts."""
    if acs is None:
        acs = []
    lines = ["# UAT Report", "", f"## Overall Result: {overall}", ""]
    if extra_top:
        lines.extend([extra_top, ""])
    for i, ac in enumerate(acs, start=1):
        lines.append(f"### [AC-{i:03d}] AC number {i}")
        lines.append(f"- **Result:** {ac.get('result', 'PASS')}")
        lines.append(f"- **Verification mode:** {ac.get('mode', 'runtime')}")
        lines.append(f"- **Driver:** {ac.get('driver', 'playwright')}")
        evidence = ac.get("evidence", ["checked the thing"])
        if evidence:
            lines.append("- **Evidence:**")
            for e in evidence:
                lines.append(f"  - {e}")
        else:
            lines.append("- **Evidence:**")
        artifacts = ac.get("artifacts", [".agentic-dev/uat_artifacts/run_1/ac_01.png"])
        if artifacts:
            lines.append("- **Artifacts:**")
            for a in artifacts:
                lines.append(f"  - {a}")
        else:
            lines.append("- **Artifacts:**")
        lines.append("")
    return "\n".join(lines)


class TestValidatorPassesGoodReport:
    """A conforming PASS report is returned unchanged."""

    def test_runtime_pass_with_artifacts_unchanged(self):
        report = _report(
            overall="PASS",
            acs=[{"result": "PASS", "mode": "runtime", "driver": "playwright"}],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert result == report
        assert "Validator Override" not in result

    def test_runtime_pass_with_monitor_style_artifacts_unchanged(self):
        """Server-log artifacts from the backgrounded-process pattern satisfy
        the same structural rules as screenshots — the validator is agnostic
        about how the artifacts were captured."""
        report = _report(
            overall="PASS",
            acs=[{
                "result": "PASS",
                "mode": "runtime",
                "driver": "playwright",
                "evidence": ["clicked submit; saw success toast"],
                "artifacts": [
                    ".agentic-dev/uat/run_1/evidence/web/ac_01.png",
                    ".agentic-dev/uat/run_1/evidence/web/server.log",
                ],
            }],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert result == report
        assert "Validator Override" not in result

    def test_fail_report_in_full_mode_unchanged(self):
        """A FAIL report has nothing to override; pass through unchanged."""
        report = _report(
            overall="FAIL",
            acs=[{"result": "FAIL", "mode": "runtime", "driver": "playwright"}],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert result == report


class TestRule1NoRuntimeAcs:
    """Rule 1: full mode + zero runtime ACs + PASS → FAIL + override."""

    def test_all_spec_traced_pass_gets_overridden(self):
        report = _report(
            overall="PASS",
            acs=[
                {"result": "PASS", "mode": "spec_trace", "driver": "none"},
                {"result": "PASS", "mode": "spec_trace", "driver": "none"},
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "## Validator Override" in result
        assert "Rule 1" in result
        assert "## Overall Result: FAIL" in result
        assert "## Overall Result: PASS" not in result

    def test_spec_only_mode_does_not_apply_rule_1(self):
        """In spec_only mode, zero-runtime-AC + PASS is allowed."""
        report = _report(
            overall="PASS",
            acs=[{"result": "PASS", "mode": "spec_trace", "driver": "none"}],
        )
        result = validate_uat_report(report, uat_mode="spec_only")
        assert "Validator Override" not in result


class TestRule2RuntimePassMissingArtifacts:
    """Rule 2: any runtime PASS AC with empty Artifacts → FAIL + override."""

    def test_empty_artifacts_triggers_override(self):
        report = _report(
            overall="PASS",
            acs=[
                {
                    "result": "PASS",
                    "mode": "runtime",
                    "driver": "playwright",
                    "artifacts": [],
                },
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "Validator Override" in result
        assert "Rule 2" in result
        assert "## Overall Result: FAIL" in result

    def test_fail_ac_with_empty_artifacts_does_not_trigger(self):
        """Rule 2 only fires when the AC claims PASS."""
        report = _report(
            overall="FAIL",
            acs=[
                {
                    "result": "FAIL",
                    "mode": "runtime",
                    "driver": "playwright",
                    "artifacts": [],
                },
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "Validator Override" not in result


class TestRule3AllDriversNone:
    """Rule 3: full mode + overall PASS + every AC has Driver: none."""

    def test_all_drivers_none_pass_overridden(self):
        report = _report(
            overall="PASS",
            acs=[
                {"result": "PASS", "mode": "runtime", "driver": "none"},
                {"result": "PASS", "mode": "runtime", "driver": "none"},
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "Validator Override" in result
        # The report has Verification mode: runtime claims but no driver actually
        # ran — at minimum rule 3 must fire (rule 2 may also fire due to artifacts).
        assert "Rule 3" in result

    def test_mixed_drivers_does_not_trigger_rule_3(self):
        report = _report(
            overall="PASS",
            acs=[
                {"result": "PASS", "mode": "runtime", "driver": "playwright"},
                {"result": "PASS", "mode": "spec_trace", "driver": "none"},
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        # Rule 3 should NOT fire (one AC has a real driver); rule 1 also fine
        # (one runtime AC exists). Report should be unchanged.
        assert result == report


class TestRule4PassWithoutEvidence:
    """Rule 4: any PASS AC without Evidence bullets → FAIL + override. Applies in both modes."""

    def test_pass_without_evidence_triggers_in_full_mode(self):
        report = _report(
            overall="PASS",
            acs=[
                {
                    "result": "PASS",
                    "mode": "runtime",
                    "driver": "playwright",
                    "evidence": [],
                },
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "Validator Override" in result
        assert "Rule 4" in result

    def test_pass_without_evidence_triggers_in_spec_only_mode(self):
        report = _report(
            overall="PASS",
            acs=[
                {
                    "result": "PASS",
                    "mode": "spec_trace",
                    "driver": "none",
                    "evidence": [],
                },
            ],
        )
        result = validate_uat_report(report, uat_mode="spec_only")
        assert "Validator Override" in result
        assert "Rule 4" in result


class TestOverrideSectionShape:
    """The ## Validator Override section lists all triggered rules and precedes the body."""

    def test_multiple_rules_all_listed(self):
        report = _report(
            overall="PASS",
            acs=[
                {
                    "result": "PASS",
                    "mode": "spec_trace",
                    "driver": "none",
                    "evidence": [],
                    "artifacts": [],
                },
            ],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert "Rule 1" in result  # no runtime AC
        assert "Rule 4" in result  # PASS without evidence

    def test_override_section_appears_before_original_body(self):
        report = _report(
            overall="PASS",
            acs=[{"result": "PASS", "mode": "spec_trace", "driver": "none"}],
        )
        result = validate_uat_report(report, uat_mode="full")
        override_idx = result.index("## Validator Override")
        body_idx = result.index("# UAT Report")
        assert override_idx < body_idx


class TestValidatorFlagsNonReport:
    """A captured chat summary (no report structure) is flagged as a hard FAIL,
    not silently persisted as a 'FAIL report' — the loud backstop for a capture
    miss (agent returned a summary instead of writing the report file)."""

    def test_summary_without_report_structure_forces_fail_override(self):
        summary = (
            "Done. Report written to .agentic-dev/uat/f022/report.md. "
            "All 10 ACs pass."
        )
        result = validate_uat_report(summary, uat_mode="full")
        assert "Validator Override" in result
        assert "## Overall Result: FAIL" in result
        # The original text is preserved after the override for debugging.
        assert "Done. Report written" in result

    def test_real_fail_report_is_left_unchanged(self):
        report = _report(
            overall="FAIL",
            acs=[{"result": "FAIL", "mode": "runtime", "driver": "playwright"}],
        )
        result = validate_uat_report(report, uat_mode="full")
        assert result == report

    def test_report_with_acs_but_no_overall_is_not_misflagged(self):
        """A report with AC structure is a (possibly malformed) report, not a
        summary — the missing-report guard must not trigger."""
        partial = (
            "### [AC-001] X\n- **Result:** PASS\n- **Evidence:**\n  - did it\n"
        )
        result = validate_uat_report(partial, uat_mode="full")
        assert "No UAT report was captured" not in result
