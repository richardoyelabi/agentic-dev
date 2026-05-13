"""Tests for Figma design onboarding."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgentRunError
from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment, MCPServerEntry
from agentic_dev.onboarding.figma import (
    FigmaMCPNotConfigured,
    NO_DESIGN_CHANGES_SENTINEL,
    _parse_design_change_result,
    check_figma_mcp_available,
    detect_design_changes,
)
from agentic_dev.onboarding.models import AnnotatedSource


SAMPLE_FIGMA_URL = "https://www.figma.com/file/abc123/MyDesign"


def _figma_env() -> ClaudeMCPEnvironment:
    """Return a ClaudeMCPEnvironment with Figma configured."""
    return ClaudeMCPEnvironment(
        servers={"figma": MCPServerEntry(name="figma", transport="stdio", source="global")}
    )


def _empty_env() -> ClaudeMCPEnvironment:
    return ClaudeMCPEnvironment(servers={})


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


class TestCheckFigmaMcpAvailable:
    def test_raises_when_figma_not_configured(self) -> None:
        with patch("agentic_dev.onboarding.figma.discover_mcp_servers", return_value=_empty_env()):
            with pytest.raises(FigmaMCPNotConfigured, match="not configured"):
                check_figma_mcp_available()

    def test_succeeds_when_figma_configured(self) -> None:
        with patch("agentic_dev.onboarding.figma.discover_mcp_servers", return_value=_figma_env()):
            check_figma_mcp_available()


class TestParseDesignChangeResult:
    def test_no_changes_sentinel_returns_no_changes(self) -> None:
        result = _parse_design_change_result(NO_DESIGN_CHANGES_SENTINEL)

        assert result.has_changes is False
        assert result.summary == ""

    def test_change_summary_returns_has_changes(self) -> None:
        text = "Button color changed from blue to red. New modal added on dashboard."
        result = _parse_design_change_result(text)

        assert result.has_changes is True
        assert text in result.summary

    def test_sentinel_anywhere_in_text(self) -> None:
        text = f"After careful analysis: {NO_DESIGN_CHANGES_SENTINEL} was found."
        result = _parse_design_change_result(text)

        assert result.has_changes is False
        assert result.summary == ""


class TestDetectDesignChanges:
    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_constructs_correct_agent_config(
        self, mock_discover: MagicMock, tmp_path: Path
    ) -> None:
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(_make_claude_result(text="some changes"))
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await detect_design_changes(mock_runner, sources, "existing spec text", tmp_path)

        config = mock_runner.run.call_args.kwargs["agent"]
        assert config.name == "design_change_detection"
        assert config.model == "opus"
        assert config.allowed_tools == []
        assert config.use_bare_mode is True

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_prompt_includes_spec_and_urls(
        self, mock_discover: MagicMock, tmp_path: Path
    ) -> None:
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(_make_claude_result(text="some changes"))
        existing_spec = "# Frontend Spec\nButton component described here."
        sources = [
            AnnotatedSource(value=SAMPLE_FIGMA_URL, annotation="Main app"),
            AnnotatedSource(value="https://www.figma.com/file/xyz/Other"),
        ]

        await detect_design_changes(mock_runner, sources, existing_spec, tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert existing_spec in prompt
        assert SAMPLE_FIGMA_URL in prompt
        assert "https://www.figma.com/file/xyz/Other" in prompt

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_returns_no_changes_when_sentinel_present(
        self, mock_discover: MagicMock, tmp_path: Path
    ) -> None:
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(
            _make_claude_result(text=f"Analysis complete. {NO_DESIGN_CHANGES_SENTINEL}")
        )
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        result = await detect_design_changes(mock_runner, sources, "spec text", tmp_path)

        assert result.has_changes is False

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_returns_changes_with_summary(
        self, mock_discover: MagicMock, tmp_path: Path
    ) -> None:
        change_text = "Header color changed. New sidebar component added."
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(_make_claude_result(text=change_text))
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        result = await detect_design_changes(mock_runner, sources, "spec text", tmp_path)

        assert result.has_changes is True
        assert change_text in result.summary

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_existing_annotations_included_in_prompt(
        self, mock_discover: MagicMock, tmp_path: Path,
    ) -> None:
        """When existing annotations are passed, they appear in the prompt
        with instructions to diff against them."""
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(_make_claude_result(text="some changes"))
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]
        existing_annotations = (
            "# Figma Annotations\n"
            "## Source: https://www.figma.com/file/abc123\n"
            "- **Button** (`123:45`): must be 44px tall for touch\n"
        )

        await detect_design_changes(
            mock_runner,
            sources,
            "spec text",
            tmp_path,
            existing_annotations=existing_annotations,
        )

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert "44px tall for touch" in prompt
        assert "Annotations" in prompt
        assert "get_annotations" in prompt

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_empty_existing_annotations_omits_annotation_section(
        self, mock_discover: MagicMock, tmp_path: Path,
    ) -> None:
        """When no existing annotations are passed, the prompt does not include
        the annotation-diff section."""
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner(_make_claude_result(text="some changes"))
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await detect_design_changes(mock_runner, sources, "spec text", tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert "Previously Persisted Designer Annotations" not in prompt

    @pytest.mark.asyncio
    @patch("agentic_dev.onboarding.figma.discover_mcp_servers")
    async def test_propagates_agent_run_error(
        self, mock_discover: MagicMock, tmp_path: Path
    ) -> None:
        mock_discover.return_value = _figma_env()
        mock_runner = _make_mock_runner()
        mock_runner.run.side_effect = AgentRunError(
            agent_name="design_change_detection",
            message="Agent failed unexpectedly",
        )
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        with pytest.raises(AgentRunError, match="design_change_detection"):
            await detect_design_changes(mock_runner, sources, "spec text", tmp_path)
