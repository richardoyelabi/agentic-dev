"""Tests for the OutputParser module."""

import json

import pytest

from agentic_dev.claude.output_parser import OutputParser
from agentic_dev.claude.runner import ClaudeResult
from agentic_dev.exceptions import OutputParseError


class TestParseJsonOutput:
    """Tests for OutputParser.parse_json_output."""

    def test_valid_json_with_all_fields(self):
        raw = json.dumps({
            "result": "The architecture is ready.",
            "session_id": "sess-xyz",
            "cost_usd": 1.23,
        })
        result = OutputParser.parse_json_output(raw, agent_name="architect")

        assert isinstance(result, ClaudeResult)
        assert result.text == "The architecture is ready."
        assert result.session_id == "sess-xyz"
        assert result.cost_usd == pytest.approx(1.23)
        assert result.exit_code == 0

    def test_valid_json_with_missing_optional_fields(self):
        raw = json.dumps({"result": "done"})
        result = OutputParser.parse_json_output(raw)

        assert result.text == "done"
        assert result.session_id is None
        assert result.cost_usd == 0.0

    def test_invalid_json_raises_output_parse_error(self):
        with pytest.raises(OutputParseError, match="Invalid JSON"):
            OutputParser.parse_json_output("{broken", agent_name="planner")

    def test_error_includes_agent_name(self):
        with pytest.raises(OutputParseError) as exc_info:
            OutputParser.parse_json_output("nope", agent_name="feature_analyst")

        assert exc_info.value.agent_name == "feature_analyst"

    def test_raw_json_preserved(self):
        data = {"result": "text", "session_id": "s1", "cost_usd": 0.5, "extra": "stuff"}
        raw = json.dumps(data)
        result = OutputParser.parse_json_output(raw)

        assert result.raw_json == data


class TestSplitDocuments:
    """Tests for OutputParser.split_documents."""

    def test_single_document_no_markers(self):
        text = "This is the entire output with no markers."
        result = OutputParser.split_documents(text, ["frontend_spec"])

        assert result == {"frontend_spec": "This is the entire output with no markers."}

    def test_single_document_with_marker(self):
        text = "<!-- DOCUMENT: frontend_spec -->\nThe spec content."
        result = OutputParser.split_documents(text, ["frontend_spec"])

        assert result["frontend_spec"] == "The spec content."

    def test_multiple_documents_split_correctly(self):
        text = (
            "<!-- DOCUMENT: frontend_spec -->\n"
            "Frontend content here.\n"
            "\n"
            "<!-- DOCUMENT: backend_spec -->\n"
            "Backend content here.\n"
            "\n"
            "<!-- DOCUMENT: api_contract -->\n"
            "API content here."
        )
        result = OutputParser.split_documents(
            text, ["frontend_spec", "backend_spec", "api_contract"], agent_name="architect"
        )

        assert result["frontend_spec"] == "Frontend content here."
        assert result["backend_spec"] == "Backend content here."
        assert result["api_contract"] == "API content here."

    def test_missing_marker_raises_output_parse_error(self):
        text = (
            "<!-- DOCUMENT: frontend_spec -->\n"
            "Some content.\n"
        )
        with pytest.raises(OutputParseError, match="Missing document marker.*backend_spec"):
            OutputParser.split_documents(
                text,
                ["frontend_spec", "backend_spec"],
                agent_name="architect",
            )

    def test_missing_marker_error_includes_agent_name(self):
        with pytest.raises(OutputParseError) as exc_info:
            OutputParser.split_documents("no markers", ["doc_a", "doc_b"], agent_name="test_agent")

        assert exc_info.value.agent_name == "test_agent"

    def test_extra_markers_are_included_in_result(self):
        text = (
            "<!-- DOCUMENT: expected -->\n"
            "Expected.\n"
            "<!-- DOCUMENT: bonus -->\n"
            "Bonus content."
        )
        result = OutputParser.split_documents(text, ["expected"])

        assert "expected" in result
        assert "bonus" in result

    def test_whitespace_stripped_from_content(self):
        text = (
            "<!-- DOCUMENT: doc_a -->\n"
            "  \n  Content with whitespace.  \n  \n"
            "<!-- DOCUMENT: doc_b -->\n"
            "\n  More content.  \n"
        )
        result = OutputParser.split_documents(text, ["doc_a", "doc_b"])

        assert result["doc_a"] == "Content with whitespace."
        assert result["doc_b"] == "More content."
