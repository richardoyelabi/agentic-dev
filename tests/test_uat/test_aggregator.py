"""Tests for the multi-track UAT aggregator."""

from agentic_dev.uat.aggregator import aggregate_uat_reports


def _report(verdict: str, body: str = "") -> str:
    return f"## Overall Result: {verdict}\n\n{body}".strip() + "\n"


def test_all_pass_returns_overall_pass():
    result = aggregate_uat_reports({
        "web": _report("PASS", "All AC verified."),
        "api": _report("PASS", "All endpoints tested."),
    })
    assert result.lstrip().startswith("## Overall Result: PASS")


def test_any_fail_returns_overall_fail():
    result = aggregate_uat_reports({
        "web": _report("PASS"),
        "api": _report("FAIL", "AC-3 broken"),
    })
    assert "## Overall Result: FAIL" in result


def test_includes_per_track_section_headers():
    result = aggregate_uat_reports({
        "web": _report("PASS"),
        "worker": _report("PASS"),
    })
    assert "# Track: web" in result
    assert "# Track: worker" in result


def test_includes_per_track_verdict_summary():
    result = aggregate_uat_reports({
        "web": _report("PASS"),
        "api": _report("FAIL"),
    })
    assert "- **web**: PASS" in result
    assert "- **api**: FAIL" in result


def test_empty_input_returns_fail():
    result = aggregate_uat_reports({})
    assert "## Overall Result: FAIL" in result


def test_missing_overall_line_treated_as_fail():
    result = aggregate_uat_reports({
        "web": "Report without overall line.",
    })
    assert "## Overall Result: FAIL" in result
    assert "- **web**: FAIL" in result


def test_label_param_customizes_section_headers():
    """Feature-level roll-up uses ``# Feature:`` headers instead of ``# Track:``."""
    result = aggregate_uat_reports(
        {
            "F001": "## Overall Result: PASS\n",
            "F002": "## Overall Result: PASS\n",
        },
        label="Feature",
    )
    assert "## Overall Result: PASS" in result
    assert "## Per-Feature Verdicts" in result
    assert "# Feature: F001" in result
    assert "# Feature: F002" in result
    assert "- **F001**: PASS" in result
