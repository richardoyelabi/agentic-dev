"""Service detection for identifying MCP service references in text.

MCP server configuration and validation is now handled by Claude Code's
native settings. See ``agentic_dev.mcp.claude_settings`` for discovery.
"""

from __future__ import annotations

import re

# Known service names for text-based detection.
KNOWN_SERVICES: list[str] = ["figma", "github", "stripe", "supabase"]

_SERVICE_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    for name in KNOWN_SERVICES
}


def detect_services_from_text(text: str) -> list[str]:
    """Scan text for references to known MCP services.

    Returns a deduplicated list of lowercase service names found.
    """
    found: list[str] = []
    for name, pattern in _SERVICE_PATTERNS.items():
        if pattern.search(text):
            found.append(name)
    return found
