"""Bridge between AgentDefinition and the ClaudeRunner's AgentConfig protocol."""

from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.agents.base import AgentDefinition


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


def to_run_config(
    agent_def: AgentDefinition,
    mcp_config: Path | None = None,
    system_prompt: str | None = None,
) -> AgentRunConfig:
    """Convert an AgentDefinition to a flat AgentRunConfig.

    Pulls claude-specific settings out of the nested ``agent_def.claude``
    object so the result satisfies the ``AgentConfig`` protocol used by
    ``ClaudeRunner``.
    """
    return AgentRunConfig(
        name=agent_def.name,
        model=agent_def.claude.model,
        permission_mode=agent_def.claude.permission_mode,
        allowed_tools=list(agent_def.claude.allowed_tools),
        max_turns=agent_def.claude.max_turns,
        use_bare_mode=agent_def.claude.use_bare_mode,
        mcp_config=mcp_config,
        system_prompt=system_prompt,
    )
