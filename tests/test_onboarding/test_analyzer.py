"""Tests for codebase onboarding analyzer."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgentRunError
from agentic_dev.onboarding.analyzer import ANALYZER_PROMPT, analyze_codebase


def _make_claude_result(
    text: str = "# Codebase Analysis\nDetected: Python backend",
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


class TestAnalyzeCodebase:
    async def test_constructs_correct_agent_config(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        await analyze_codebase(mock_runner, tmp_path)

        config = mock_runner.run.call_args.kwargs["agent"]
        assert config.name == "onboarding_analyzer"
        assert config.model == "sonnet"
        assert config.permission_mode == "plan"
        assert config.allowed_tools == ["Read", "Glob", "Grep"]
        assert config.max_turns == 30
        assert config.use_bare_mode is True
        assert config.mcp_config is None
        assert config.system_prompt is None

    async def test_passes_analyzer_prompt(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        await analyze_codebase(mock_runner, tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert prompt == ANALYZER_PROMPT

    async def test_passes_codebase_path_as_working_dir(self, tmp_path: Path) -> None:
        codebase_path = tmp_path / "my-codebase"
        codebase_path.mkdir()
        mock_runner = _make_mock_runner()

        await analyze_codebase(mock_runner, codebase_path)

        working_dir = mock_runner.run.call_args.kwargs["working_dir"]
        assert working_dir == codebase_path

    async def test_returns_claude_result(self, tmp_path: Path) -> None:
        expected = _make_claude_result(
            text="# Codebase Analysis\nReact + Express",
            cost_usd=0.42,
        )
        mock_runner = _make_mock_runner(return_value=expected)

        result = await analyze_codebase(mock_runner, tmp_path)

        assert result is expected

    async def test_propagates_agent_run_error(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        mock_runner.run.side_effect = AgentRunError(
            agent_name="onboarding_analyzer",
            message="timeout after 30 turns",
        )

        with pytest.raises(AgentRunError, match="onboarding_analyzer"):
            await analyze_codebase(mock_runner, tmp_path)
