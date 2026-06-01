"""Bridge between AgentDefinition and the ClaudeRunner's AgentConfig protocol."""

from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.agents.base import AgentDefinition
from agentic_dev.agents.figma_tools import figma_tool_patterns


@dataclass
class AgentRunConfig:
    """Flat configuration that satisfies the AgentConfig protocol.

    Extracts nested ClaudeConfig fields from an AgentDefinition into the
    top-level attributes expected by ClaudeRunner.
    """

    name: str
    model: str
    permission_mode: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 50
    use_bare_mode: bool = True
    mcp_config: Path | None = None
    system_prompt: str | None = None
    timeout_s: int | None = None
    idle_timeout_s: int | None = None


def to_run_config(
    agent_def: AgentDefinition,
    mcp_config: Path | None = None,
    system_prompt: str | None = None,
    figma_mcp_enabled: bool = False,
) -> AgentRunConfig:
    """Convert an AgentDefinition to a flat AgentRunConfig.

    Pulls claude-specific settings out of the nested ``agent_def.claude``
    object so the result satisfies the ``AgentConfig`` protocol used by
    ``ClaudeRunner``.

    When the agent definition opts into Figma MCP (``claude.figma_mcp =
    true``) AND the project has Figma sources configured
    (``figma_mcp_enabled=True``), the Figma MCP wildcard patterns are
    appended to ``allowed_tools``. Both gates must agree — the YAML flag
    is the agent's opt-in; the runtime flag is the project's gate.
    """
    allowed_tools = list(agent_def.claude.allowed_tools)
    if agent_def.claude.figma_mcp and figma_mcp_enabled:
        allowed_tools.extend(figma_tool_patterns())
    return AgentRunConfig(
        name=agent_def.name,
        model=agent_def.claude.model,
        permission_mode=agent_def.claude.permission_mode,
        allowed_tools=allowed_tools,
        max_turns=agent_def.claude.max_turns,
        use_bare_mode=agent_def.claude.use_bare_mode,
        mcp_config=mcp_config,
        system_prompt=system_prompt,
        timeout_s=agent_def.claude.timeout_s,
        idle_timeout_s=agent_def.claude.idle_timeout_s,
    )
