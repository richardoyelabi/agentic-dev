"""Figma design integration for the agency workflow.

Provides helpers for checking Figma MCP availability, persisting Figma
source URLs, and detecting design changes between update cycles by
comparing the live Figma state against existing specs.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agentic_dev.agents.figma_tools import figma_tool_patterns
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.mcp.claude_settings import discover_mcp_servers, find_server_for_service
from agentic_dev.onboarding.models import AnnotatedSource
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig


NO_DESIGN_CHANGES_SENTINEL = "NO_DESIGN_CHANGES"


class DesignChangeResult(BaseModel):
    """Result of comparing live Figma designs against existing specs."""

    has_changes: bool
    summary: str


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


def figma_mcp_available_flag(has_sources: bool) -> str:
    """Return ``"true"``/``"false"`` for whether UI agents can use Figma.

    Resolves to ``"true"`` only when there are Figma sources to inspect *and*
    a Figma MCP server is configured in Claude Code. Templates gate their
    design-token and design-fidelity instructions on this string, and the
    orchestrator uses it to decide whether to expand the Figma MCP tool
    patterns for agents that opt in via ``claude.figma_mcp``.
    """
    if not has_sources:
        return "false"
    try:
        check_figma_mcp_available()
    except FigmaMCPNotConfigured:
        return "false"
    return "true"


def write_figma_sources(doc_store: DocumentStore, sources: list[AnnotatedSource]) -> None:
    """Persist Figma URLs and annotations to a ``figma_sources`` document.

    Writes nothing if *sources* is empty.
    """
    if not sources:
        return

    lines = ["# Figma Sources", ""]
    for src in sources:
        lines.append(f"- URL: {src.value}")
        if src.annotation:
            lines.append(f"  Annotation: {src.annotation}")
        lines.append("")

    doc_store.write("figma_sources", "\n".join(lines))


def _parse_design_change_result(text: str) -> DesignChangeResult:
    """Parse agent output into a DesignChangeResult.

    If the output contains the ``NO_DESIGN_CHANGES`` sentinel the result
    is marked as having no changes.  Otherwise the full text is treated
    as the change summary.
    """
    if NO_DESIGN_CHANGES_SENTINEL in text:
        return DesignChangeResult(has_changes=False, summary="")
    return DesignChangeResult(has_changes=True, summary=text.strip())


async def detect_design_changes(
    claude: ClaudeRunner,
    sources: list[AnnotatedSource],
    existing_spec: str,
    working_dir: Path,
    existing_annotations: str = "",
) -> DesignChangeResult:
    """Detect design changes by comparing live Figma state against existing specs.

    Uses a Claude agent with Figma MCP tools to inspect the current
    design files and compare them against what the frontend spec describes.
    Only reports actual structural/visual design changes — not phrasing
    differences between LLM-generated documents.

    Args:
        claude: The ClaudeRunner instance.
        sources: Figma URLs with optional annotations.
        existing_spec: The current frontend_spec text to compare against.
        working_dir: Working directory for the agent.
        existing_annotations: Optional prior ``figma_annotations`` doc. When
            non-empty, the agent is asked to diff designer annotations as a
            separate class of design change.

    Returns:
        DesignChangeResult indicating whether changes were found and a summary.
    """
    config = AgentRunConfig(
        name="design_change_detection",
        model="opus",
        permission_mode="bypassPermissions",
        allowed_tools=figma_tool_patterns(),
        max_turns=15,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

    figma_urls_block = "\n".join(
        f"- {src.value}" + (f" ({src.annotation})" if src.annotation else "")
        for src in sources
    )

    renderer = PromptRenderer()
    prompt = renderer.render(
        "design_change_detection.md.j2",
        {
            "existing_spec": existing_spec,
            "figma_urls": figma_urls_block,
            "sentinel": NO_DESIGN_CHANGES_SENTINEL,
            "existing_annotations": existing_annotations,
        },
    )

    result = await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=working_dir,
    )
    return _parse_design_change_result(result.text)
