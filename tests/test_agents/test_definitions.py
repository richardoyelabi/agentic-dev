"""Tests for shipped agent YAML definitions.

These guardrails lock in non-default flags that matter for runtime
behaviour (figma_mcp opt-in, architect's drill-down toolset) so that
silent edits to the YAMLs are caught.
"""

from agentic_dev.agents.registry import AgentRegistry


def test_architect_has_codebase_read_tools():
    reg = AgentRegistry()
    architect = reg.get("architect")
    assert architect.claude.allowed_tools == ["Read", "Glob", "Grep"]


def test_architect_max_turns_bumped_for_drill_downs():
    reg = AgentRegistry()
    architect = reg.get("architect")
    assert architect.claude.max_turns >= 15


def test_architect_opts_into_figma_mcp():
    reg = AgentRegistry()
    architect = reg.get("architect")
    assert architect.claude.figma_mcp is True


def test_developer_opts_into_figma_mcp():
    reg = AgentRegistry()
    developer = reg.get("developer")
    assert developer.claude.figma_mcp is True


def test_design_change_detection_opts_into_figma_mcp():
    reg = AgentRegistry()
    agent = reg.get("design_change_detection")
    assert agent.claude.figma_mcp is True


def test_figma_mcp_defaults_false_on_other_agents():
    reg = AgentRegistry()
    qa = reg.get("qa")
    assert qa.claude.figma_mcp is False
