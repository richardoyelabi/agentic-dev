"""CLI integration tests for project onboarding with --from-codebase and --from-figma."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentic_dev.claude.runner import ClaudeResult
from agentic_dev.cli import app
from agentic_dev.exceptions import AgentRunError


runner = CliRunner()


@pytest.fixture(autouse=True)
def _bypass_mcp_check():
    """Bypass MCP prerequisite validation in CLI tests."""
    with patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True):
        yield


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
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_and_figma_together(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Django backend",
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

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Build a dashboard" in content
        assert "Codebase Analysis" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/abc" not in content

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()
        assert "https://figma.com/file/abc" in figma_sources_path.read_text(encoding="utf-8")

    @patch("agentic_dev.cli._run_pipeline")
    def test_from_figma_alone(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
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

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Build a landing page" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/xyz" not in content

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()
        assert "https://figma.com/file/xyz" in figma_sources_path.read_text(encoding="utf-8")

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_with_empty_user_input(
        self,
        mock_analyze_codebase,
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
        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=False)
    def test_from_figma_mcp_not_configured(
        self,
        mock_mcp,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
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
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_analyzer_fails(
        self,
        mock_analyze_codebase,
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
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_both_flags_no_user_requirements(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Express API",
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

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/abc" not in content

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()


class TestMultiSourceOnboarding:
    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_multiple_codebases(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.side_effect = [
            _make_claude_result(text="# Codebase Analysis\nFrontend: React"),
            _make_claude_result(text="# Codebase Analysis\nBackend: Django"),
        ]

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/path/frontend",
                "--from-codebase", "/path/backend",
            ],
            input="Extend this app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        assert mock_analyze_codebase.call_count == 2

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Frontend: React" in content
        assert "Backend: Django" in content

    @patch("agentic_dev.cli._run_pipeline")
    def test_multiple_figma(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/a",
                "--from-figma", "https://figma.com/file/b",
            ],
            input="Build the app\n\n\n",
        )

        assert result.exit_code == 0, result.output

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()
        figma_sources_content = figma_sources_path.read_text(encoding="utf-8")
        assert "https://figma.com/file/a" in figma_sources_content
        assert "https://figma.com/file/b" in figma_sources_content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_codebase_with_annotation(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nReact SPA",
        )

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/path/frontend::Frontend React app",
            ],
            input="Extend this\n\n\n",
        )

        assert result.exit_code == 0, result.output

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Source: Codebase - Frontend React app" in content
        assert "/path/frontend" in content

        # Verify annotation was passed to the analyzer (3rd positional arg)
        call_args = mock_analyze_codebase.call_args
        annotation = call_args.kwargs.get("annotation") or call_args[0][2]
        assert annotation == "Frontend React app"

    @patch("agentic_dev.cli._run_pipeline")
    def test_figma_with_annotation(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/abc::Admin dashboard",
            ],
            input="Build the admin\n\n\n",
        )

        assert result.exit_code == 0, result.output

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()
        figma_sources_content = figma_sources_path.read_text(encoding="utf-8")
        assert "https://figma.com/file/abc" in figma_sources_content
        assert "Admin dashboard" in figma_sources_content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_mixed_multiple_sources(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.side_effect = [
            _make_claude_result(text="# Codebase Analysis\nReact frontend"),
            _make_claude_result(text="# Codebase Analysis\nExpress API"),
        ]

        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-codebase", "/path/fe::Frontend",
                "--from-codebase", "/path/be::Backend API",
                "--from-figma", "https://figma.com/file/a::App UI",
                "--from-figma", "https://figma.com/file/b::Design tokens",
            ],
            input="Extend everything\n\n\n",
        )

        assert result.exit_code == 0, result.output
        assert mock_analyze_codebase.call_count == 2

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Source: Codebase - Frontend" in content
        assert "Source: Codebase - Backend API" in content
        # Figma URLs should not be concatenated into user_input
        assert "figma.com/file/a" not in content

        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()

    @patch("agentic_dev.cli._run_pipeline")
    def test_figma_sources_contains_all_urls_with_annotations(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/a::App UI",
                "--from-figma", "https://figma.com/file/b::Admin Panel",
            ],
            input="Build the app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        figma_sources_path = tmp_path / "my-app" / "docs" / "figma_sources.md"
        assert figma_sources_path.exists()
        content = figma_sources_path.read_text(encoding="utf-8")
        assert "https://figma.com/file/a" in content
        assert "https://figma.com/file/b" in content
        assert "App UI" in content
        assert "Admin Panel" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_backward_compat_single_codebase(
        self,
        mock_analyze_codebase,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        mock_analyze_codebase.return_value = _make_claude_result(
            text="# Codebase Analysis\nDetected: Python",
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

        assert result.exit_code == 0, result.output
        mock_analyze_codebase.assert_called_once()

        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content
