"""CLI integration tests for project onboarding with --from-codebase and --from-figma."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentic_dev.claude.runner import ClaudeResult
from agentic_dev.cli import app
from agentic_dev.exceptions import AgentRunError
from agentic_dev.onboarding.figma import FigmaMCPNotConfigured


runner = CliRunner()


def _make_claude_result(
    text: str = "Test output",
    session_id: str = "test-session",
    cost_usd: float = 0.10,
    exit_code: int = 0,
) -> ClaudeResult:
    return ClaudeResult(
        text=text,
        session_id=session_id,
        cost_usd=cost_usd,
        exit_code=exit_code,
        raw_json={},
    )


class TestOnboardingCLI:
    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.figma.analyze_figma_design")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_and_figma_together(
        self,
        mock_analyze_codebase,
        mock_analyze_figma,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Django backend",
        )
        mock_analyze_figma.return_value = _make_claude_result(
            text="# Design Analysis\nPages: Home, Settings",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/some/path",
                "--from-figma", "https://figma.com/file/abc",
            ],
            input="Build a dashboard\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_analyze_codebase.assert_called_once()
        mock_analyze_figma.assert_called_once()

        user_input_path = tmp_path / "my-app" / "docs" / "user_input"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Build a dashboard" in content
        assert "Codebase Analysis" in content
        assert "Design Analysis" in content
        codebase_pos = content.index("Codebase Analysis")
        design_pos = content.index("Design Analysis")
        assert codebase_pos < design_pos

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.figma.analyze_figma_design")
    def test_from_figma_alone(
        self,
        mock_analyze_figma,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_figma.return_value = _make_claude_result(
            text="# Design Analysis\nComponents: Navbar, Footer",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/xyz",
            ],
            input="Build a landing page\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_analyze_figma.assert_called_once()

        user_input_path = tmp_path / "my-app" / "docs" / "user_input"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Design Analysis" in content
        assert "Build a landing page" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_with_empty_user_input(
        self,
        mock_analyze_codebase,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Flask API",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/some/path",
            ],
            input="\n\n\n",
        )

        assert result.exit_code == 0, result.output
        user_input_path = tmp_path / "my-app" / "docs" / "user_input"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.figma.analyze_figma_design")
    def test_from_figma_mcp_not_configured(
        self,
        mock_analyze_figma,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_figma.side_effect = FigmaMCPNotConfigured()

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/abc",
            ],
            input="Build something\n\n\n",
        )

        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_analyzer_fails(
        self,
        mock_analyze_codebase,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.side_effect = AgentRunError(
            agent_name="onboarding_analyzer",
            message="timeout after 30 turns",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/some/path",
            ],
            input="Extend this app\n\n\n",
        )

        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.figma.analyze_figma_design")
    def test_from_figma_agent_fails(
        self,
        mock_analyze_figma,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_figma.side_effect = AgentRunError(
            agent_name="onboarding_figma",
            message="rate limited",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/abc",
            ],
            input="Build something\n\n\n",
        )

        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.workspace.git.init_repo")
    @patch("agentic_dev.onboarding.figma.analyze_figma_design")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_both_flags_no_user_requirements(
        self,
        mock_analyze_codebase,
        mock_analyze_figma,
        mock_init_repo,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Express API",
        )
        mock_analyze_figma.return_value = _make_claude_result(
            text="# Design Analysis\nTokens: blue-500, gray-100",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/some/path",
                "--from-figma", "https://figma.com/file/abc",
            ],
            input="\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_run_pipeline.assert_called_once()

        user_input_path = tmp_path / "my-app" / "docs" / "user_input"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content
        assert "Design Analysis" in content
