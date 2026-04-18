"""Tests for the UAT report composer."""

import pytest

from agentic_dev.orchestrator.uat_composer import compose_remediation_input
from agentic_dev.state.models import FrontendKind


class TestComposeRemediationInput:
    def test_compose_includes_uat_report(self) -> None:
        uat_report = "FAIL: Missing empty state handling for notes list."
        result = compose_remediation_input(uat_report, "my-app", frontend_kind=None)

        assert uat_report in result

    def test_compose_includes_project_name(self) -> None:
        result = compose_remediation_input("some report", "note-taking-app", frontend_kind=None)

        assert "note-taking-app" in result

    def test_compose_includes_remediation_header(self) -> None:
        result = compose_remediation_input("report content", "my-app", frontend_kind=None)

        assert "Remediation Request" in result

    def test_compose_includes_preservation_instruction(self) -> None:
        result = compose_remediation_input("report content", "my-app", frontend_kind=None)

        assert "Preserve all currently passing functionality" in result

    def test_compose_includes_modify_instruction(self) -> None:
        result = compose_remediation_input("report content", "my-app", frontend_kind=None)

        assert "modify existing code" in result
        assert "do not start from scratch" in result


class TestComposeRemediationInputFrontendKindPhrasing:
    @pytest.mark.parametrize(
        "frontend_kind,expected_phrase",
        [
            (FrontendKind.WEB, "backend and frontend"),
            (FrontendKind.CLI, "backend and CLI"),
            (FrontendKind.DESKTOP, "backend and desktop app"),
            (FrontendKind.MOBILE, "backend and mobile app"),
            (FrontendKind.NONE, "backend"),
        ],
    )
    def test_each_kind_produces_expected_phrase(
        self, frontend_kind, expected_phrase
    ):
        result = compose_remediation_input(
            "report content", "my-app", frontend_kind=frontend_kind
        )
        assert expected_phrase in result

    def test_none_kind_falls_back_to_generic_existing(self):
        result = compose_remediation_input(
            "report content", "my-app", frontend_kind=None
        )
        assert "existing" in result
