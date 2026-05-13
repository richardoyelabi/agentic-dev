"""Tests for Figma designer-annotation extraction."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.config import AGENTIC_DEV_METADATA_DIR
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.onboarding.figma_annotations import (
    extract_figma_annotations,
    write_figma_annotations,
)
from agentic_dev.onboarding.models import AnnotatedSource


SAMPLE_FIGMA_URL = "https://www.figma.com/file/abc123/MyDesign"


def _make_claude_result(
    text: str = "# Figma Annotations\n- Frame A: must be 44px tall",
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


@pytest.fixture
def doc_store(tmp_path: Path) -> DocumentStore:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir()
    return DocumentStore(project_dir)


class TestExtractFigmaAnnotations:
    async def test_constructs_correct_agent_config(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await extract_figma_annotations(mock_runner, sources, tmp_path)

        config = mock_runner.run.call_args.kwargs["agent"]
        assert config.name == "figma_annotations_extractor"
        assert config.model == "sonnet"
        assert config.permission_mode == "bypassPermissions"
        assert config.use_bare_mode is True
        assert config.mcp_config is None
        assert config.system_prompt is None

    async def test_allowed_tools_include_figma_mcp_patterns(
        self, tmp_path: Path,
    ) -> None:
        """The extractor must let the agent call Figma MCP tools."""
        mock_runner = _make_mock_runner()
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await extract_figma_annotations(mock_runner, sources, tmp_path)

        config = mock_runner.run.call_args.kwargs["agent"]
        joined = ",".join(config.allowed_tools)
        assert "figma" in joined.lower()

    async def test_prompt_includes_all_figma_urls(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        sources = [
            AnnotatedSource(value=SAMPLE_FIGMA_URL, annotation="Main UI"),
            AnnotatedSource(value="https://www.figma.com/file/xyz/Other"),
        ]

        await extract_figma_annotations(mock_runner, sources, tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert SAMPLE_FIGMA_URL in prompt
        assert "https://www.figma.com/file/xyz/Other" in prompt
        assert "Main UI" in prompt

    async def test_prompt_instructs_calling_get_annotations(
        self, tmp_path: Path,
    ) -> None:
        mock_runner = _make_mock_runner()
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await extract_figma_annotations(mock_runner, sources, tmp_path)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert "get_annotations" in prompt

    async def test_returns_claude_result(self, tmp_path: Path) -> None:
        expected = _make_claude_result(text="# Figma Annotations\n- foo: 44px")
        mock_runner = _make_mock_runner(return_value=expected)
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        result = await extract_figma_annotations(mock_runner, sources, tmp_path)

        assert result is expected

    async def test_uses_working_dir(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        await extract_figma_annotations(mock_runner, sources, tmp_path)

        working_dir = mock_runner.run.call_args.kwargs["working_dir"]
        assert working_dir == tmp_path

    async def test_raises_on_empty_sources(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        with pytest.raises(ValueError, match="at least one"):
            await extract_figma_annotations(mock_runner, [], tmp_path)

        mock_runner.run.assert_not_called()

    async def test_propagates_agent_run_error(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        mock_runner.run.side_effect = AgentRunError(
            agent_name="figma_annotations_extractor",
            message="timeout",
        )
        sources = [AnnotatedSource(value=SAMPLE_FIGMA_URL)]

        with pytest.raises(AgentRunError, match="figma_annotations_extractor"):
            await extract_figma_annotations(mock_runner, sources, tmp_path)


class TestWriteFigmaAnnotations:
    def test_persists_text_to_figma_annotations_doc(
        self, doc_store: DocumentStore,
    ) -> None:
        write_figma_annotations(doc_store, "# Figma Annotations\n- A: 44px")

        assert doc_store.exists("figma_annotations")
        assert "44px" in doc_store.read("figma_annotations")

    def test_overwrites_existing_doc(self, doc_store: DocumentStore) -> None:
        write_figma_annotations(doc_store, "# Figma Annotations\n- old: old text")
        write_figma_annotations(doc_store, "# Figma Annotations\n- new: new text")

        content = doc_store.read("figma_annotations")
        assert "new text" in content
        assert "old text" not in content

    def test_empty_text_does_not_write(self, doc_store: DocumentStore) -> None:
        write_figma_annotations(doc_store, "")

        assert not doc_store.exists("figma_annotations")

    def test_whitespace_only_text_does_not_write(
        self, doc_store: DocumentStore,
    ) -> None:
        write_figma_annotations(doc_store, "   \n\t  \n")

        assert not doc_store.exists("figma_annotations")
