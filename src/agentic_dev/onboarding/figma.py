"""Figma design import for onboarding existing designs into the agency workflow.

Uses a Claude agent with the Figma MCP server to extract design information
and produce a Design Analysis document.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.config import MCP_CONFIGS_DIR
from agentic_dev.exceptions import AgenticDevError
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
            "Figma MCP server is not configured. "
            "To use Figma onboarding, configure the Figma MCP server. "
            "See https://docs.anthropic.com/en/docs/claude-code/mcp "
            "for MCP server configuration instructions."
        )


def get_figma_mcp_config() -> Path:
    """Get the path to the Figma MCP config file.

    Returns:
        Path to the Figma MCP config.

    Raises:
        FigmaMCPNotConfigured: If no Figma MCP config exists.
    """
    figma_config = MCP_CONFIGS_DIR / "figma.json"
    if not figma_config.exists():
        raise FigmaMCPNotConfigured()
    return figma_config


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
    mcp_config = get_figma_mcp_config()

    config = AgentRunConfig(
        name="onboarding_figma",
        model="sonnet",
        permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=30,
        use_bare_mode=True,
        mcp_config=mcp_config,
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
