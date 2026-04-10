"""Figma design import for onboarding existing designs into the agency workflow.

Uses a Claude agent with the Figma MCP server to extract design information
and produce a Design Analysis document.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.mcp.claude_settings import discover_mcp_servers, find_server_for_service
from agentic_dev.onboarding.models import AnnotatedSource
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig


FIGMA_PROMPT_TEMPLATE = """\
You are an expert UI/UX analyst. Using the Figma MCP tools available to you, \
analyze the Figma file at the following URL:

{figma_url}

Extract all design information and produce a structured Design Analysis document \
in the following format:

# Design Analysis

## Pages
### <page name>
- **Layout:** <description of layout structure>
- **Components:** <list of components used>

## Components
### <component name>
- **Purpose:** <what it represents>
- **Variants:** <variants if any>
- **Props:** <configurable properties>

## Design Tokens
- **Colors:** <color palette with hex values>
- **Typography:** <font families, sizes, weights>
- **Spacing:** <spacing scale>

## Navigation
- <describe the navigation structure and user flows>

## Notes
- <anything notable about the design that would inform development>
"""


class FigmaMCPNotConfigured(AgenticDevError):
    """Raised when Figma MCP server is not available."""

    def __init__(self) -> None:
        super().__init__(
            "Figma MCP server is not configured in your Claude Code settings. "
            "Run 'claude mcp add figma' or use Claude Code's authentication UI "
            "to connect Figma (supports OAuth). "
            "See https://docs.anthropic.com/en/docs/claude-code/mcp for details."
        )


def check_figma_mcp_available() -> None:
    """Verify that a Figma MCP server is configured in Claude Code.

    Raises:
        FigmaMCPNotConfigured: If no Figma MCP server is found.
    """
    env = discover_mcp_servers()
    if find_server_for_service(env, "figma") is None:
        raise FigmaMCPNotConfigured()


async def analyze_figma_design(
    claude: ClaudeRunner,
    figma_url: str,
    working_dir: Path,
    annotation: str = "",
) -> ClaudeResult:
    """Analyze a Figma design using a Claude agent with Figma MCP tools.

    Args:
        claude: The ClaudeRunner instance.
        figma_url: URL to the Figma file to analyze.
        working_dir: Working directory for the agent.
        annotation: Optional human description of what this Figma file represents
            (e.g. "Main app UI", "Admin dashboard").

    Returns:
        ClaudeResult containing the Design Analysis document.

    Raises:
        FigmaMCPNotConfigured: If Figma MCP server is not configured.
    """
    check_figma_mcp_available()

    config = AgentRunConfig(
        name="onboarding_figma",
        model="sonnet",
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=30,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    prompt = FIGMA_PROMPT_TEMPLATE.format(figma_url=figma_url)
    if annotation:
        prompt = (
            f"Context: This Figma file is described as: \"{annotation}\"\n\n"
            + prompt
        )

    return await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=working_dir,
    )


async def analyze_figma_designs(
    claude: ClaudeRunner,
    sources: list[AnnotatedSource],
    working_dir: Path,
) -> list[ClaudeResult]:
    """Analyze multiple Figma design files concurrently.

    Args:
        claude: The ClaudeRunner instance.
        sources: List of annotated Figma URL sources to analyze.
        working_dir: Working directory for the agents.

    Returns:
        List of ClaudeResults in the same order as the input sources.

    Raises:
        FigmaMCPNotConfigured: If Figma MCP server is not configured.
    """
    tasks = [
        analyze_figma_design(claude, src.value, working_dir, src.annotation)
        for src in sources
    ]
    return list(await asyncio.gather(*tasks))
