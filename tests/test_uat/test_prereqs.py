"""Tests for UAT runtime prereq probes."""

from unittest.mock import MagicMock, patch

import pytest

from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment, MCPServerEntry
from agentic_dev.state.models import FrontendKind, ProjectType
from agentic_dev.uat.prereqs import (
    PrereqResult,
    check_prereqs,
    render_doc,
)


def _ok_proc(stdout: str = "ok", returncode: int = 0) -> MagicMock:
    """Build a subprocess.CompletedProcess-like mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _fail_proc(stderr: str = "not found") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = stderr
    return proc


@pytest.fixture
def env_with_playwright() -> ClaudeMCPEnvironment:
    return ClaudeMCPEnvironment(
        servers={
            "playwright": MCPServerEntry(
                name="playwright", transport="stdio", source="plugin"
            )
        }
    )


@pytest.fixture
def env_empty() -> ClaudeMCPEnvironment:
    return ClaudeMCPEnvironment(servers={})


class TestCheckPrereqsWeb:
    """uat_web requires Playwright MCP + npx."""

    def test_all_present_returns_ok(self, env_with_playwright):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_with_playwright), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc("9.0.0")):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.WEB)
        assert result.ok is True
        assert result.agent_name == "uat_web"
        assert result.missing == []

    def test_missing_playwright_mcp_flags_missing(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc()):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.WEB)
        assert result.ok is False
        assert any("playwright" in m.lower() for m in result.missing)

    def test_missing_npx_flags_missing(self, env_with_playwright):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_with_playwright), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=FileNotFoundError()):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.WEB)
        assert result.ok is False
        assert any("npx" in m.lower() for m in result.missing)


class TestCheckPrereqsCli:
    """uat_cli has no special prereqs beyond Bash (always present)."""

    def test_cli_always_ok(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.CLI)
        assert result.ok is True
        assert result.agent_name == "uat_cli"


class TestCheckPrereqsDesktopElectron:
    """uat_desktop_electron needs Playwright MCP + npx."""

    def test_electron_present(self, env_with_playwright):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_with_playwright), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc()):
            result = check_prereqs(
                ProjectType.FULLSTACK, FrontendKind.DESKTOP, desktop_framework="electron"
            )
        assert result.ok is True
        assert result.agent_name == "uat_desktop_electron"


class TestCheckPrereqsDesktopTauri:
    """uat_desktop_tauri needs `tauri-driver` on PATH."""

    def test_tauri_driver_present(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc("tauri-driver 0.1.3")):
            result = check_prereqs(
                ProjectType.FULLSTACK, FrontendKind.DESKTOP, desktop_framework="tauri"
            )
        assert result.ok is True
        assert result.agent_name == "uat_desktop_tauri"

    def test_tauri_driver_missing(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=FileNotFoundError()):
            result = check_prereqs(
                ProjectType.FULLSTACK, FrontendKind.DESKTOP, desktop_framework="tauri"
            )
        assert result.ok is False
        assert any("tauri-driver" in m for m in result.missing)


class TestCheckPrereqsMobile:
    """uat_mobile needs Maestro+device OR Flutter+device."""

    def test_maestro_with_doctor_ok(self, env_empty):
        def run_side_effect(cmd, *args, **kwargs):
            if "maestro" in cmd[0]:
                return _ok_proc("1.35.0")
            return _fail_proc()
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=run_side_effect):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.MOBILE)
        assert result.ok is True

    def test_flutter_with_devices_ok(self, env_empty):
        def run_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "flutter" and cmd[1] == "--version":
                return _ok_proc("Flutter 3.16.0")
            if cmd[0] == "flutter" and cmd[1] == "devices":
                return _ok_proc("Pixel 7 (emulator-5554)\niPhone 15 (booted)")
            return _fail_proc()
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=run_side_effect):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.MOBILE)
        assert result.ok is True

    def test_maestro_installed_but_no_device_fails(self, env_empty):
        """Binary-present-but-runtime-missing: maestro on PATH but `maestro doctor` fails."""
        def run_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "maestro" and cmd[1] == "--version":
                return _ok_proc("1.35.0")
            if cmd[0] == "maestro" and cmd[1] == "doctor":
                return _fail_proc("No device detected")
            return _fail_proc()
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=run_side_effect):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.MOBILE)
        assert result.ok is False
        assert any("maestro" in m.lower() or "flutter" in m.lower() for m in result.missing)

    def test_flutter_devices_lists_only_web_fails(self, env_empty):
        """Flutter present but only web target counts as missing for mobile UAT."""
        def run_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "flutter" and cmd[1] == "--version":
                return _ok_proc("Flutter 3.16.0")
            if cmd[0] == "flutter" and cmd[1] == "devices":
                return _ok_proc("Chrome (web)\nWeb Server (web-javascript)")
            return _fail_proc()
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=run_side_effect):
            result = check_prereqs(ProjectType.FULLSTACK, FrontendKind.MOBILE)
        assert result.ok is False


class TestCheckPrereqsApi:
    """uat_api needs curl or httpx."""

    def test_curl_present(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc("curl 8.4.0")):
            result = check_prereqs(ProjectType.BACKEND_ONLY, FrontendKind.NONE)
        assert result.ok is True
        assert result.agent_name == "uat_api"


class TestRenderDoc:
    """render_doc produces uat_prereqs markdown."""

    def test_renders_agent_name_and_probes(self):
        result = PrereqResult(
            agent_name="uat_web",
            probes=[],
            missing=[],
            ok=True,
        )
        doc = render_doc(result)
        assert "uat_web" in doc
        assert "Prereqs" in doc or "prereqs" in doc.lower()

    def test_renders_missing_probes(self):
        result = PrereqResult(
            agent_name="uat_mobile",
            probes=[],
            missing=["maestro (not on PATH)", "flutter (no device)"],
            ok=False,
        )
        doc = render_doc(result)
        assert "maestro" in doc
        assert "flutter" in doc
        assert "FAIL" in doc or "fail" in doc.lower() or "missing" in doc.lower()


class TestEventEmission:
    """check_prereqs emits UATPrereqValidationEvent when there are missing tools."""

    def test_event_emitted_on_missing_tools(self, env_empty):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_empty), \
             patch("agentic_dev.uat.prereqs.subprocess.run", side_effect=FileNotFoundError()), \
             patch("agentic_dev.uat.prereqs.emit") as mock_emit:
            check_prereqs(ProjectType.FULLSTACK, FrontendKind.WEB)
        assert mock_emit.called

    def test_no_event_when_all_ok(self, env_with_playwright):
        with patch("agentic_dev.uat.prereqs.discover_mcp_servers", return_value=env_with_playwright), \
             patch("agentic_dev.uat.prereqs.subprocess.run", return_value=_ok_proc()), \
             patch("agentic_dev.uat.prereqs.emit") as mock_emit:
            check_prereqs(ProjectType.FULLSTACK, FrontendKind.WEB)
        assert not mock_emit.called
