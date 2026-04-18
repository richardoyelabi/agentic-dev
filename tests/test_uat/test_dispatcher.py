"""Tests for the UAT agent dispatcher."""

import pytest

from agentic_dev.state.models import FrontendKind, ProjectType
from agentic_dev.uat.dispatcher import (
    _read_desktop_framework,
    pick_uat_agent,
)


class TestPickUatAgentValidCombos:
    """Valid (ProjectType, FrontendKind) combos return the documented agent."""

    @pytest.mark.parametrize(
        "project_type,frontend_kind,expected",
        [
            (ProjectType.FULLSTACK, FrontendKind.WEB, "uat_web"),
            (ProjectType.FULLSTACK, FrontendKind.CLI, "uat_cli"),
            (ProjectType.FULLSTACK, FrontendKind.MOBILE, "uat_mobile"),
            (ProjectType.FRONTEND_ONLY, FrontendKind.WEB, "uat_web"),
            (ProjectType.FRONTEND_ONLY, FrontendKind.CLI, "uat_cli"),
            (ProjectType.FRONTEND_ONLY, FrontendKind.MOBILE, "uat_mobile"),
            (ProjectType.BACKEND_ONLY, FrontendKind.NONE, "uat_api"),
        ],
    )
    def test_non_desktop_combos(self, project_type, frontend_kind, expected):
        assert pick_uat_agent(project_type, frontend_kind) == expected

    @pytest.mark.parametrize("project_type", [ProjectType.FULLSTACK, ProjectType.FRONTEND_ONLY])
    def test_desktop_electron(self, project_type):
        assert (
            pick_uat_agent(project_type, FrontendKind.DESKTOP, desktop_framework="electron")
            == "uat_desktop_electron"
        )

    @pytest.mark.parametrize("project_type", [ProjectType.FULLSTACK, ProjectType.FRONTEND_ONLY])
    def test_desktop_tauri(self, project_type):
        assert (
            pick_uat_agent(project_type, FrontendKind.DESKTOP, desktop_framework="tauri")
            == "uat_desktop_tauri"
        )


class TestPickUatAgentInvalidCombos:
    """Invalid combos raise ValueError with both axes named in the message."""

    @pytest.mark.parametrize(
        "project_type,frontend_kind",
        [
            (ProjectType.FULLSTACK, FrontendKind.NONE),
            (ProjectType.FRONTEND_ONLY, FrontendKind.NONE),
            (ProjectType.BACKEND_ONLY, FrontendKind.WEB),
            (ProjectType.BACKEND_ONLY, FrontendKind.CLI),
            (ProjectType.BACKEND_ONLY, FrontendKind.DESKTOP),
            (ProjectType.BACKEND_ONLY, FrontendKind.MOBILE),
        ],
    )
    def test_invalid_combo_raises(self, project_type, frontend_kind):
        with pytest.raises(ValueError) as exc:
            pick_uat_agent(project_type, frontend_kind)
        # Error must name both axes so debugging isn't a guess-fest.
        assert str(project_type.value) in str(exc.value)
        assert str(frontend_kind.value) in str(exc.value)


class TestPickUatAgentDesktopFrameworkErrors:
    """Desktop dispatch requires a known framework."""

    def test_missing_framework_raises(self):
        with pytest.raises(ValueError) as exc:
            pick_uat_agent(ProjectType.FULLSTACK, FrontendKind.DESKTOP)
        assert "desktop_framework" in str(exc.value)

    def test_unknown_framework_raises(self):
        with pytest.raises(ValueError) as exc:
            pick_uat_agent(
                ProjectType.FULLSTACK, FrontendKind.DESKTOP, desktop_framework="qt"
            )
        assert "qt" in str(exc.value)


class TestReadDesktopFramework:
    """`_read_desktop_framework` extracts `desktop_framework:` header from spec text."""

    def test_extracts_electron(self):
        text = (
            "# Frontend Spec\n\n"
            "## Frontend Kind\ndesktop\n\n"
            "## desktop_framework\nelectron\n\n"
            "## Components\n..."
        )
        assert _read_desktop_framework(text) == "electron"

    def test_extracts_tauri(self):
        text = "## desktop_framework: tauri\n"
        assert _read_desktop_framework(text) == "tauri"

    def test_returns_none_when_absent(self):
        text = "# Frontend Spec\n\n## Components\n..."
        assert _read_desktop_framework(text) is None

    def test_case_insensitive_value(self):
        text = "## desktop_framework: Electron\n"
        assert _read_desktop_framework(text) == "electron"

    def test_unknown_framework_returns_none(self):
        text = "## desktop_framework: qt\n"
        assert _read_desktop_framework(text) is None
