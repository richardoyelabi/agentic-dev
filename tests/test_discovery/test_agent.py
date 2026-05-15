"""Tests for the project-discovery Claude agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.discovery.agent import (
    DISCOVERY_PROMPT,
    DiscoveryResult,
    discover_tracks,
    parse_discovery_response,
)
from agentic_dev.exceptions import AgenticDevError


def _claude_result(text: str) -> ClaudeResult:
    return ClaudeResult(
        text=text,
        session_id="test-session",
        cost_usd=0.10,
        exit_code=0,
        raw_json={},
    )


def _mock_runner(text: str) -> MagicMock:
    runner = MagicMock(spec=ClaudeRunner)
    runner.run = AsyncMock(return_value=_claude_result(text))
    return runner


class TestParseDiscoveryResponse:
    def test_parses_strict_json_single_track(self) -> None:
        text = (
            '{"tracks": [{"name": "app", "path": ".", "kind": "web", '
            '"uat_kind": "web"}], "reasoning": "single Next.js app at root"}'
        )

        result = parse_discovery_response(text)

        assert isinstance(result, DiscoveryResult)
        assert len(result.tracks) == 1
        assert result.tracks[0].name == "app"
        assert result.tracks[0].path == "."
        assert result.tracks[0].kind == "web"
        assert result.tracks[0].uat_kind == "web"
        assert "Next.js" in result.reasoning

    def test_parses_two_track_repo(self) -> None:
        text = (
            '{"tracks": ['
            '{"name": "backend", "path": "backend", "kind": "api", "uat_kind": "api"},'
            '{"name": "frontend", "path": "frontend", "kind": "web", "uat_kind": "web"}'
            '], "reasoning": "two codebases"}'
        )

        result = parse_discovery_response(text)

        assert {t.name for t in result.tracks} == {"backend", "frontend"}
        assert {t.kind for t in result.tracks} == {"api", "web"}

    def test_extracts_json_from_markdown_fence(self) -> None:
        text = (
            "Sure, here you go:\n\n"
            "```json\n"
            '{"tracks": [{"name": "api", "path": ".", "kind": "api"}], '
            '"reasoning": "FastAPI"}\n'
            "```\n"
        )

        result = parse_discovery_response(text)

        assert result.tracks[0].name == "api"
        assert result.tracks[0].uat_kind is None

    def test_empty_text_raises(self) -> None:
        with pytest.raises(AgenticDevError, match="no JSON"):
            parse_discovery_response("")

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(AgenticDevError, match="invalid JSON"):
            parse_discovery_response("{not valid json}")

    def test_missing_tracks_list_raises(self) -> None:
        with pytest.raises(AgenticDevError, match="no tracks"):
            parse_discovery_response('{"tracks": [], "reasoning": "empty"}')

    def test_invalid_track_slug_raises(self) -> None:
        # Track validator rejects names that don't match ``[a-z0-9_-]+``.
        with pytest.raises(Exception):  # pydantic ValidationError
            parse_discovery_response(
                '{"tracks": [{"name": "Has Space", "kind": "web"}]}'
            )


class TestDiscoverTracks:
    @pytest.mark.asyncio
    async def test_runs_agent_in_project_root(self, tmp_path: Path) -> None:
        runner = _mock_runner(
            '{"tracks": [{"name": "app", "path": ".", "kind": "web"}], '
            '"reasoning": "one app"}'
        )

        result = await discover_tracks(runner, tmp_path)

        runner.run.assert_awaited_once()
        kwargs = runner.run.call_args.kwargs
        assert kwargs["working_dir"] == tmp_path
        assert kwargs["prompt"] == DISCOVERY_PROMPT
        assert result.tracks[0].name == "app"

    @pytest.mark.asyncio
    async def test_uses_correct_agent_config(self, tmp_path: Path) -> None:
        runner = _mock_runner(
            '{"tracks": [{"name": "x", "kind": "web"}], "reasoning": "."}'
        )

        await discover_tracks(runner, tmp_path)

        config = runner.run.call_args.kwargs["agent"]
        assert config.name == "project_discovery"
        assert config.model == "sonnet"
        assert config.permission_mode == "plan"
        assert config.allowed_tools == ["Read", "Glob", "Grep"]
        assert config.use_bare_mode is True
        assert config.system_prompt is None
