"""Shared Figma MCP tool wildcard patterns.

The Figma MCP server can be registered under several different names —
``figma-mcp-go``, ``figma-remote-mcp``, or a user-added stdio entry simply
called ``figma``. Agents that opt into Figma access need to whitelist all
three so they work regardless of which is configured in the user's Claude
Code settings.

Kept in a single place so the YAML-driven agents (architect, developer,
design_change_detection) and the inline-config helpers (figma annotations
extractor, design change detection) share one source of truth.
"""

from __future__ import annotations


_FIGMA_MCP_SERVER_NAMES = (
    "figma-mcp-go",
    "figma-remote-mcp",
    "figma",
)


def figma_tool_patterns() -> list[str]:
    """Return wildcard patterns for every known Figma MCP server name.

    Returns a fresh list each call so callers can safely ``extend`` or
    ``append`` without mutating shared state.
    """
    return [f"mcp__{name}__*" for name in _FIGMA_MCP_SERVER_NAMES]
