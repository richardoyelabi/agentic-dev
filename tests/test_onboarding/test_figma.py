"""Tests for Figma design onboarding."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgentRunError
from agentic_dev.onboarding.figma import (
    FIGMA_PROMPT_TEMPLATE,
    FigmaMCPNotConfigured,
    analyze_figma_design,
    analyze_figma_designs,
    get_figma_mcp_config,
)
from agentic_dev.onboarding.models import AnnotatedSource


SAMPLE_FIGMA_URL = "https://www.figma.com/file/abc123/MyDesign"


def _make_claude_result(
    text: str = "# Design Analysis\nPages: Home, Dashboard",
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


def _make_mock_runner(return_value: ClaudeResult | None = None) -> MagicMock:
    mock = MagicMock(spec=ClaudeRunner)
    mock.run = AsyncMock(return_value=return_value or _make_claude_result())
    return mock


class TestGetFigmaMcpConfig:
    def test_raises_when_figma_json_missing(self) -> None:
        with patch("agentic_dev.onboarding.figma.get_mcp_config_path", return_value=None):
            with pytest.raises(FigmaMCPNotConfigured, match="not configured"):
                get_figma_mcp_config()

    def test_returns_path_when_figma_json_exists(self, tmp_path: Path) -> None:
        figma_config = tmp_path / "figma.json"
        figma_config.write_text("{}", encoding="utf-8")

        with patch("agentic_dev.onboarding.figma.get_mcp_config_path", return_value=figma_config):
            result = get_figma_mcp_config()

        assert result == figma_config


class TestAnalyzeFigmaDesign:
    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_constructs_correct_agent_config_with_mcp(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        fake_mcp_path = Path("/fake/figma.json")
        mock_get_config.return_value = fake_mcp_path
        mock_runner = _make_mock_runner()

        await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

        config = mock_runner.run.call_args.kwargs["agent"]
        assert config.name == "onboarding_figma"
        assert config.model == "sonnet"
        assert config.permission_mode == "plan"
        assert config.allowed_tools == ["Read", "Glob", "Grep"]
        assert config.max_turns == 30
        assert config.use_bare_mode is True
        assert config.mcp_config == fake_mcp_path
        assert config.system_prompt is None

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_formats_prompt_with_figma_url(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        mock_runner = _make_mock_runner()

        await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert SAMPLE_FIGMA_URL in prompt
        assert "Design Analysis" in prompt

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_passes_working_dir(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        mock_runner = _make_mock_runner()

        await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

        working_dir = mock_runner.run.call_args.kwargs["working_dir"]
        assert working_dir == tmp_path

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_returns_claude_result(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        expected = _make_claude_result(
            text="# Design Analysis\nComponents: Button, Card",
            cost_usd=0.35,
        )
        mock_runner = _make_mock_runner(return_value=expected)

        result = await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

        assert result is expected

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_raises_figma_mcp_not_configured(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.side_effect = FigmaMCPNotConfigured()
        mock_runner = _make_mock_runner()

        with pytest.raises(FigmaMCPNotConfigured):
            await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

        mock_runner.run.assert_not_called()

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_propagates_agent_run_error(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        mock_runner = _make_mock_runner()
        mock_runner.run.side_effect = AgentRunError(
            agent_name="onboarding_figma",
            message="MCP connection failed",
        )

        with pytest.raises(AgentRunError, match="onboarding_figma"):
            await analyze_figma_design(mock_runner, SAMPLE_FIGMA_URL, tmp_path)

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_annotation_prepended_to_prompt(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        mock_runner = _make_mock_runner()

        await analyze_figma_design(
            mock_runner, SAMPLE_FIGMA_URL, tmp_path, annotation="Admin dashboard"
        )

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert prompt.startswith("Context: This Figma file is described as:")
        assert "Admin dashboard" in prompt
        assert SAMPLE_FIGMA_URL in prompt

    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_empty_annotation_uses_original_prompt(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        mock_runner = _make_mock_runner()

        await analyze_figma_design(
            mock_runner, SAMPLE_FIGMA_URL, tmp_path, annotation=""
        )

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        expected = FIGMA_PROMPT_TEMPLATE.format(figma_url=SAMPLE_FIGMA_URL)
        assert prompt == expected


class TestAnalyzeFigmaDesigns:
    @patch("agentic_dev.onboarding.figma.get_figma_mcp_config")
    async def test_runs_all_sources(
        self, mock_get_config: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_config.return_value = Path("/fake/figma.json")
        results = [
            _make_claude_result(text="Design 1"),
            _make_claude_result(text="Design 2"),
        ]
        mock_runner = MagicMock(spec=ClaudeRunner)
        mock_runner.run = AsyncMock(side_effect=results)

        sources = [
            AnnotatedSource(value="https://figma.com/file/a", annotation="App UI"),
            AnnotatedSource(value="https://figma.com/file/b", annotation="Admin"),
        ]
        actual = await analyze_figma_designs(mock_runner, sources, tmp_path)

        assert len(actual) == 2
        assert actual[0].text == "Design 1"
        assert actual[1].text == "Design 2"
        assert mock_runner.run.call_count == 2
