"""Tests for the MCP setup helper."""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from agentic_dev.mcp.setup import check_mcp_prerequisites, guide_figma_setup


def _write_settings(path: Path, mcp_servers: dict) -> None:
    """Helper to write a Claude Code settings file with mcpServers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": mcp_servers}, indent=2))


def _devnull_console() -> Console:
    return Console(file=open(os.devnull, "w"))


class TestCheckMcpPrerequisites:
    """Tests for check_mcp_prerequisites()."""

    def test_returns_true_when_service_configured(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "figma": {"command": "npx", "args": ["-y", "@anthropic-ai/figma-mcp-server"]}
        })
        console = _devnull_console()
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            result = check_mcp_prerequisites(["figma"], console)
        assert result is True

    def test_returns_false_when_service_not_configured(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        console = _devnull_console()
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            result = check_mcp_prerequisites(["figma"], console)
        assert result is False

    def test_returns_true_for_empty_services_list(self) -> None:
        console = _devnull_console()
        result = check_mcp_prerequisites([], console)
        assert result is True

    def test_returns_false_if_any_service_not_configured(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "figma": {"command": "npx", "args": ["-y", "@anthropic-ai/figma-mcp-server"]}
        })
        console = _devnull_console()
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            result = check_mcp_prerequisites(["figma", "stripe"], console)
        assert result is False

    def test_prints_status_table(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "figma": {"command": "npx", "args": ["-y", "@anthropic-ai/figma-mcp-server"]}
        })
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            check_mcp_prerequisites(["figma"], console)
        rendered = output.getvalue()
        assert "figma" in rendered.lower()

    def test_prints_setup_guidance_for_missing_service(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            check_mcp_prerequisites(["figma"], console)
        rendered = output.getvalue()
        assert "claude mcp add" in rendered

    def test_uses_project_dir_for_discovery(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        project_dir = tmp_path / "project"
        _write_settings(project_dir / ".claude" / "settings.json", {
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}
        })
        console = _devnull_console()
        with patch("agentic_dev.mcp.claude_settings.DEFAULT_CLAUDE_HOME", claude_home):
            result = check_mcp_prerequisites(
                ["github"], console, project_dir=project_dir
            )
        assert result is True


class TestGuideFigmaSetup:
    """Tests for guide_figma_setup()."""

    def test_prints_claude_mcp_add_instruction(self) -> None:
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        guide_figma_setup(console)
        rendered = output.getvalue()
        assert "claude mcp add" in rendered

    def test_mentions_oauth(self) -> None:
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        guide_figma_setup(console)
        rendered = output.getvalue()
        assert "OAuth" in rendered

    def test_prints_docs_url(self) -> None:
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        guide_figma_setup(console)
        rendered = output.getvalue()
        assert "docs.anthropic.com" in rendered
