"""Tests for the MCP setup helper."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from rich.console import Console

from agentic_dev.mcp.setup import check_mcp_prerequisites, guide_figma_setup


class TestCheckMcpPrerequisites:
    """Tests for check_mcp_prerequisites()."""

    def test_returns_true_when_all_services_ready(self) -> None:
        console = Console(file=open(os.devnull, "w"))
        with patch.dict(os.environ, {"FIGMA_ACCESS_TOKEN": "test-token"}):
            result = check_mcp_prerequisites(["figma"], console)
        assert result is True

    def test_returns_false_when_env_var_missing(self) -> None:
        console = Console(file=open(os.devnull, "w"))
        env = os.environ.copy()
        env.pop("FIGMA_ACCESS_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_mcp_prerequisites(["figma"], console)
        assert result is False

    def test_returns_false_for_unknown_service(self) -> None:
        console = Console(file=open(os.devnull, "w"))
        result = check_mcp_prerequisites(["nonexistent"], console)
        assert result is False

    def test_returns_true_for_empty_services_list(self) -> None:
        console = Console(file=open(os.devnull, "w"))
        result = check_mcp_prerequisites([], console)
        assert result is True

    def test_returns_false_if_any_service_not_ready(self) -> None:
        console = Console(file=open(os.devnull, "w"))
        env = os.environ.copy()
        env.pop("STRIPE_API_KEY", None)
        env["FIGMA_ACCESS_TOKEN"] = "test-token"
        with patch.dict(os.environ, env, clear=True):
            result = check_mcp_prerequisites(["figma", "stripe"], console)
        assert result is False

    def test_prints_status_table(self) -> None:
        from io import StringIO
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        with patch.dict(os.environ, {"FIGMA_ACCESS_TOKEN": "test-token"}):
            check_mcp_prerequisites(["figma"], console)
        rendered = output.getvalue()
        assert "figma" in rendered.lower() or "Figma" in rendered

    def test_prints_setup_instructions_for_failing_service(self) -> None:
        from io import StringIO
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        env = os.environ.copy()
        env.pop("FIGMA_ACCESS_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            check_mcp_prerequisites(["figma"], console)
        rendered = output.getvalue()
        assert "FIGMA_ACCESS_TOKEN" in rendered


class TestGuideFigmaSetup:
    """Tests for guide_figma_setup()."""

    def test_prints_figma_token_instructions(self) -> None:
        from io import StringIO
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        guide_figma_setup(console)
        rendered = output.getvalue()
        assert "FIGMA_ACCESS_TOKEN" in rendered

    def test_prints_figma_url(self) -> None:
        from io import StringIO
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        guide_figma_setup(console)
        rendered = output.getvalue()
        assert "figma.com" in rendered
