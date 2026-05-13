"""CLI integration tests for project onboarding with --from-codebase and --from-figma."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

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


@pytest.fixture(autouse=True)
def _bypass_figma_annotations_extractor(request):
    """Bypass the Figma annotation extractor by default.

    Without this, every CLI test that passes ``--from-figma`` would invoke a
    real ``claude`` subprocess via the ClaudeRunner inside
    ``_extract_and_persist_figma_annotations``. Tests that need to exercise
    the extractor wiring opt out with ``@pytest.mark.no_bypass_figma_extractor``.
    """
    if "no_bypass_figma_extractor" in request.keywords:
        yield
        return
    with patch("agentic_dev.cli._extract_and_persist_figma_annotations"):
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Build a dashboard" in content
        assert "Codebase Analysis" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/abc" not in content

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Build a landing page" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/xyz" not in content

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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
        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content
        # Figma URL should not be concatenated into user_input
        assert "figma.com/file/abc" not in content

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
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

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
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

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Source: Codebase - Frontend" in content
        assert "Source: Codebase - Backend API" in content
        # Figma URLs should not be concatenated into user_input
        assert "figma.com/file/a" not in content

        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
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
        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
        assert figma_sources_path.exists()
        content = figma_sources_path.read_text(encoding="utf-8")
        assert "https://figma.com/file/a" in content
        assert "https://figma.com/file/b" in content
        assert "App UI" in content
        assert "Admin Panel" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._extract_and_persist_figma_annotations")
    def test_from_figma_invokes_annotation_extractor(
        self,
        mock_extract,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        """--from-figma should call the annotation extractor after persisting sources."""
        result = runner.invoke(
            app,
            [
                "new", "my-app",
                "--path", str(tmp_path),
                "--from-figma", "https://figma.com/file/abc::Main UI",
            ],
            input="Build a dashboard\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_extract.assert_called_once()
        # The extractor receives the parsed figma sources
        sources_arg = mock_extract.call_args[0][0]
        assert len(sources_arg) == 1
        assert sources_arg[0].value == "https://figma.com/file/abc"
        assert sources_arg[0].annotation == "Main UI"

    @pytest.mark.no_bypass_figma_extractor
    @patch("agentic_dev.cli._run_pipeline")
    def test_extractor_persists_figma_annotations_doc(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        """When the extractor returns text, it should land in figma_annotations.md."""
        canned_text = (
            "# Figma Annotations\n"
            "## Source: https://figma.com/file/abc\n"
            "- **Login** (`1:2`): must be 44px tall\n"
        )

        with patch(
            "agentic_dev.onboarding.figma_annotations.extract_figma_annotations",
            new_callable=AsyncMock,
            return_value=_make_claude_result(text=canned_text),
        ):
            result = runner.invoke(
                app,
                [
                    "new", "my-app",
                    "--path", str(tmp_path),
                    "--from-figma", "https://figma.com/file/abc",
                ],
                input="Build a dashboard\n\n\n",
            )

        assert result.exit_code == 0, result.output

        annotations_path = (
            tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_annotations.md"
        )
        assert annotations_path.exists()
        assert "44px tall" in annotations_path.read_text(encoding="utf-8")

    @pytest.mark.no_bypass_figma_extractor
    @patch("agentic_dev.cli._run_pipeline")
    def test_extractor_failure_does_not_block_pipeline(
        self,
        mock_run_pipeline,
        tmp_path: Path,
    ) -> None:
        """If the extractor raises, the pipeline still proceeds and no doc is written."""
        with patch(
            "agentic_dev.onboarding.figma_annotations.extract_figma_annotations",
            new_callable=AsyncMock,
            side_effect=AgentRunError(
                agent_name="figma_annotations_extractor",
                message="timeout",
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "new", "my-app",
                    "--path", str(tmp_path),
                    "--from-figma", "https://figma.com/file/abc",
                ],
                input="Build a dashboard\n\n\n",
            )

        assert result.exit_code == 0, result.output
        # figma_sources is still persisted
        figma_sources_path = (
            tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
        )
        assert figma_sources_path.exists()
        # figma_annotations doc is absent because the extractor failed
        annotations_path = (
            tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_annotations.md"
        )
        assert not annotations_path.exists()
        mock_run_pipeline.assert_called_once()

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

        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content
