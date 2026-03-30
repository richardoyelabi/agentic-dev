"""Tests for the UAT report composer."""

from agentic_dev.orchestrator.uat_composer import compose_remediation_input


class TestComposeRemediationInput:
    def test_compose_includes_uat_report(self) -> None:
        uat_report = "FAIL: Missing empty state handling for notes list."
        result = compose_remediation_input(uat_report, "my-app")

        assert uat_report in result

    def test_compose_includes_project_name(self) -> None:
        result = compose_remediation_input("some report", "note-taking-app")

        assert "note-taking-app" in result

    def test_compose_includes_remediation_header(self) -> None:
        result = compose_remediation_input("report content", "my-app")

        assert "Remediation Request" in result

    def test_compose_includes_preservation_instruction(self) -> None:
        result = compose_remediation_input("report content", "my-app")

        assert "Preserve all currently passing functionality" in result

    def test_compose_includes_modify_instruction(self) -> None:
        result = compose_remediation_input("report content", "my-app")

        assert "modify existing code" in result
        assert "do not start from scratch" in result
