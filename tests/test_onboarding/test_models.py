"""Tests for onboarding data models."""

from agentic_dev.onboarding.models import AnnotatedSource


class TestAnnotatedSourceParse:
    def test_parse_value_only(self) -> None:
        result = AnnotatedSource.parse("/path/to/code")
        assert result.value == "/path/to/code"
        assert result.annotation == ""

    def test_parse_with_annotation(self) -> None:
        result = AnnotatedSource.parse("/path/to/code::Frontend React app")
        assert result.value == "/path/to/code"
        assert result.annotation == "Frontend React app"

    def test_parse_with_empty_annotation(self) -> None:
        result = AnnotatedSource.parse("/path/to/code::")
        assert result.value == "/path/to/code"
        assert result.annotation == ""

    def test_parse_with_multiple_delimiters(self) -> None:
        result = AnnotatedSource.parse("https://figma.com/file/abc::Main UI::v2")
        assert result.value == "https://figma.com/file/abc"
        assert result.annotation == "Main UI::v2"

    def test_parse_strips_whitespace(self) -> None:
        result = AnnotatedSource.parse("  /path/to/code  ::  Frontend app  ")
        assert result.value == "/path/to/code"
        assert result.annotation == "Frontend app"
