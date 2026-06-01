"""Tests for ``to_run_config`` Figma MCP tool injection."""

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.figma_tools import figma_tool_patterns
from agentic_dev.orchestrator.agent_bridge import to_run_config


def _agent(figma_mcp: bool, allowed_tools: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        name="t",
        description="t",
        team="t",
        claude=ClaudeConfig(
            model="sonnet",
            permission_mode="bypassPermissions",
            allowed_tools=allowed_tools if allowed_tools is not None else ["Read"],
            max_budget_usd=1.00,
            figma_mcp=figma_mcp,
        ),
        prompt_template="t.md.j2",
        input_documents=[],
    )


class TestFigmaToolInjection:
    def test_no_figma_flag_anywhere_omits_patterns(self):
        cfg = to_run_config(_agent(figma_mcp=False))
        assert all(not p.startswith("mcp__figma") for p in cfg.allowed_tools)

    def test_agent_opted_in_but_project_disabled_omits_patterns(self):
        cfg = to_run_config(_agent(figma_mcp=True), figma_mcp_enabled=False)
        assert all(not p.startswith("mcp__figma") for p in cfg.allowed_tools)

    def test_project_enabled_but_agent_not_opted_in_omits_patterns(self):
        cfg = to_run_config(_agent(figma_mcp=False), figma_mcp_enabled=True)
        assert all(not p.startswith("mcp__figma") for p in cfg.allowed_tools)

    def test_both_flags_true_appends_all_figma_patterns(self):
        cfg = to_run_config(_agent(figma_mcp=True), figma_mcp_enabled=True)
        for pattern in figma_tool_patterns():
            assert pattern in cfg.allowed_tools

    def test_figma_patterns_are_appended_not_replacing(self):
        cfg = to_run_config(
            _agent(figma_mcp=True, allowed_tools=["Read", "Glob"]),
            figma_mcp_enabled=True,
        )
        assert cfg.allowed_tools[:2] == ["Read", "Glob"]
        assert "mcp__figma-mcp-go__*" in cfg.allowed_tools

    def test_does_not_mutate_agent_definition_allowed_tools(self):
        agent = _agent(figma_mcp=True, allowed_tools=["Read"])
        to_run_config(agent, figma_mcp_enabled=True)
        assert agent.claude.allowed_tools == ["Read"]
