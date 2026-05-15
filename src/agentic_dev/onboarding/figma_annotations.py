"""Designer annotation extraction for Figma sources.

Runs a Claude agent with access to the Figma MCP server to call
``get_annotations`` against each Figma URL and produce a structured
``figma_annotations`` document. The persisted doc is then fed into the
architect, developer (UI tracks), and design-change-detection agents.

This module is intentionally separate from :mod:`agentic_dev.onboarding.figma`
to keep the user-typed source ``annotation`` (a free-text label like
"Admin dashboard") distinct from Figma's native annotations (designer
comments on frames).
"""

from __future__ import annotations

from pathlib import Path

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.onboarding.models import AnnotatedSource
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig
from agentic_dev.prompts.renderer import PromptRenderer


_FIGMA_MCP_TOOL_PATTERNS = [
    "mcp__figma-mcp-go",
    "mcp__figma-remote-mcp",
    "mcp__figma",
]


def _allowed_tools_for_figma() -> list[str]:
    """Return the wildcard tool patterns the extractor agent may invoke.

    The figma MCP server can be registered under several different names
    (``figma-mcp-go``, ``figma-remote-mcp``, or simply ``figma`` for
    user-added stdio servers). Allow them all so the agent can use whichever
    one is configured.
    """
    return [f"{prefix}__*" for prefix in _FIGMA_MCP_TOOL_PATTERNS]


async def extract_figma_annotations(
    claude: ClaudeRunner,
    sources: list[AnnotatedSource],
    working_dir: Path,
) -> ClaudeResult:
    """Extract designer annotations from a list of Figma URLs.

    Runs a single Sonnet agent with Figma MCP tools available. The agent
    is responsible for calling ``get_annotations`` per URL and emitting a
    structured markdown document. The caller is responsible for persisting
    the result via :func:`write_figma_annotations`.

    Args:
        claude: The ClaudeRunner instance.
        sources: Figma URLs (with optional user-typed labels).
        working_dir: Working directory for the agent.

    Returns:
        ClaudeResult containing the Figma Annotations document.

    Raises:
        ValueError: If *sources* is empty.
        AgentRunError: Propagated from the underlying Claude invocation.
    """
    if not sources:
        raise ValueError(
            "extract_figma_annotations requires at least one Figma source."
        )

    config = AgentRunConfig(
        name="figma_annotations_extractor",
        model="sonnet",
        permission_mode="bypassPermissions",
        allowed_tools=_allowed_tools_for_figma(),
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    figma_urls_block = "\n".join(
        f"- {src.value}" + (f" ({src.annotation})" if src.annotation else "")
        for src in sources
    )

    renderer = PromptRenderer()
    prompt = renderer.render(
        "figma_annotations_extractor.md.j2",
        {"figma_urls": figma_urls_block},
    )

    return await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=working_dir,
    )


def write_figma_annotations(doc_store: DocumentStore, text: str) -> None:
    """Persist extractor output to the ``figma_annotations`` document.

    Writes nothing if *text* is empty or whitespace-only — the extractor
    can legitimately produce no output when the Figma MCP server is
    misconfigured or when no annotations exist.
    """
    if not text or not text.strip():
        return
    doc_store.write("figma_annotations", text)
