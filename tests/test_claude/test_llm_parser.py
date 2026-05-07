"""Tests for the shared LLM-as-parser helper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from agentic_dev.claude.llm_parser import _extract_json_block, parse_with_llm
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import OutputParseError


class _Item(BaseModel):
    name: str
    count: int


class _Container(BaseModel):
    items: list[_Item] = Field(default_factory=list)


def _result(text: str) -> ClaudeResult:
    return ClaudeResult(text=text, session_id="s", cost_usd=0.0, exit_code=0)


def _fenced(payload: str) -> str:
    return f"```json\n{payload}\n```"


@pytest.fixture
def claude() -> ClaudeRunner:
    runner = MagicMock(spec=ClaudeRunner)
    runner.run = AsyncMock()
    return runner


class TestExtractJsonBlock:
    """Tests for _extract_json_block."""

    def test_extracts_fenced_json_block(self):
        text = "Here is the data:\n```json\n{\"a\": 1}\n```\nDone."
        assert _extract_json_block(text) == '{"a": 1}'

    def test_falls_back_to_any_fenced_block(self):
        text = "```\n[1, 2, 3]\n```"
        assert _extract_json_block(text) == "[1, 2, 3]"

    def test_falls_back_to_raw_object_span(self):
        text = "Sure thing: {\"a\": 1, \"b\": [2]}  trailing prose"
        assert _extract_json_block(text) == '{"a": 1, "b": [2]}'

    def test_falls_back_to_raw_array_span(self):
        text = "result = [1, 2, 3]"
        assert _extract_json_block(text) == "[1, 2, 3]"


class TestParseWithLLM:
    """Tests for parse_with_llm."""

    async def test_returns_validated_model_on_success(self, claude):
        claude.run.return_value = _result(_fenced('{"items": [{"name": "x", "count": 1}]}'))

        result = await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract items",
            working_dir=Path("/tmp"),
        )

        assert isinstance(result, _Container)
        assert len(result.items) == 1
        assert result.items[0].name == "x"
        assert claude.run.await_count == 1

    async def test_retries_on_invalid_json_then_succeeds(self, claude):
        claude.run.side_effect = [
            _result("not json at all"),
            _result(_fenced('{"items": []}')),
        ]

        result = await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract",
            working_dir=Path("/tmp"),
            max_attempts=2,
        )

        assert isinstance(result, _Container)
        assert claude.run.await_count == 2

    async def test_retries_on_schema_validation_then_succeeds(self, claude):
        claude.run.side_effect = [
            _result(_fenced('{"items": [{"name": "x"}]}')),  # missing count
            _result(_fenced('{"items": [{"name": "x", "count": 5}]}')),
        ]

        result = await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract",
            working_dir=Path("/tmp"),
            max_attempts=2,
        )

        assert result.items[0].count == 5
        assert claude.run.await_count == 2

    async def test_retries_on_sanity_failure_then_succeeds(self, claude):
        claude.run.side_effect = [
            _result(_fenced('{"items": [{"name": "x", "count": 99}]}')),
            _result(_fenced('{"items": [{"name": "x", "count": 1}]}')),
        ]

        def sanity_check(parsed: _Container) -> None:
            if parsed.items[0].count != 1:
                raise ValueError("count must be 1")

        result = await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract",
            working_dir=Path("/tmp"),
            sanity_check=sanity_check,
            max_attempts=2,
        )

        assert result.items[0].count == 1
        assert claude.run.await_count == 2

    async def test_raises_output_parse_error_after_exhausting_attempts(self, claude):
        claude.run.side_effect = [
            _result("garbage 1"),
            _result("garbage 2"),
        ]

        with pytest.raises(OutputParseError) as excinfo:
            await parse_with_llm(
                claude=claude,
                text="dummy",
                schema_model=_Container,
                extraction_prompt="extract",
                working_dir=Path("/tmp"),
                max_attempts=2,
            )

        assert "after 2 attempt" in str(excinfo.value)
        assert claude.run.await_count == 2

    async def test_includes_prior_error_in_retry_prompt(self, claude):
        claude.run.side_effect = [
            _result("totally not json"),
            _result(_fenced('{"items": []}')),
        ]

        await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract",
            working_dir=Path("/tmp"),
            max_attempts=2,
        )

        retry_prompt = claude.run.await_args_list[1].kwargs["prompt"]
        assert "previous response failed validation" in retry_prompt.lower()

    async def test_empty_response_counts_as_failure(self, claude):
        claude.run.side_effect = [
            _result(""),
            _result(_fenced('{"items": []}')),
        ]

        await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="extract",
            working_dir=Path("/tmp"),
            max_attempts=2,
        )

        assert claude.run.await_count == 2

    async def test_schema_is_embedded_in_prompt(self, claude):
        claude.run.return_value = _result(_fenced('{"items": []}'))

        await parse_with_llm(
            claude=claude,
            text="dummy",
            schema_model=_Container,
            extraction_prompt="my custom instructions",
            working_dir=Path("/tmp"),
        )

        prompt = claude.run.await_args.kwargs["prompt"]
        assert "my custom instructions" in prompt
        assert "items" in prompt  # schema property
        assert "DOCUMENT" in prompt
