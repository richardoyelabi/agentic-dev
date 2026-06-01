"""Tests for the shared Figma MCP tool patterns."""

from agentic_dev.agents.figma_tools import figma_tool_patterns


def test_returns_wildcard_for_each_known_server():
    patterns = figma_tool_patterns()
    assert patterns == [
        "mcp__figma-mcp-go__*",
        "mcp__figma-remote-mcp__*",
        "mcp__figma__*",
    ]


def test_returns_a_new_list_each_call():
    """Callers may mutate the returned list (e.g. extend allowed_tools)."""
    a = figma_tool_patterns()
    a.append("extra")
    b = figma_tool_patterns()
    assert "extra" not in b


def test_legacy_alias_in_figma_annotations_module_still_works():
    """The original private helper is re-exported for back-compat."""
    from agentic_dev.onboarding.figma_annotations import _allowed_tools_for_figma

    assert _allowed_tools_for_figma() == figma_tool_patterns()
