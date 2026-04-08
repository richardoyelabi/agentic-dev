"""Central catalog of known MCP services, validation, and config merging."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from agentic_dev.config import MCP_CONFIGS_DIR


class MCPServiceInfo(BaseModel):
    """Describes a known MCP service and its requirements."""

    name: str
    config_file: str
    required_env_vars: list[str] = Field(default_factory=list)
    setup_instructions: str = ""
    install_hint: str = ""


class MCPValidationResult(BaseModel):
    """Result of validating an MCP service's readiness."""

    service_name: str
    config_exists: bool = False
    missing_env_vars: list[str] = Field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        """Service is ready when config exists and all env vars are set."""
        return self.config_exists and len(self.missing_env_vars) == 0


SERVICE_CATALOG: dict[str, MCPServiceInfo] = {
    "figma": MCPServiceInfo(
        name="Figma",
        config_file="figma.json",
        required_env_vars=["FIGMA_ACCESS_TOKEN"],
        setup_instructions=(
            "1. Go to https://www.figma.com/developers/api#access-tokens\n"
            "2. Generate a personal access token\n"
            "3. Export it: export FIGMA_ACCESS_TOKEN=<your-token>"
        ),
        install_hint="npx -y @anthropic-ai/figma-mcp-server",
    ),
    "github": MCPServiceInfo(
        name="GitHub",
        config_file="github.json",
        required_env_vars=["GITHUB_TOKEN"],
        setup_instructions=(
            "1. Go to https://github.com/settings/tokens\n"
            "2. Create a personal access token with repo scope\n"
            "3. Export it: export GITHUB_TOKEN=<your-token>"
        ),
        install_hint="npx -y @modelcontextprotocol/server-github",
    ),
    "stripe": MCPServiceInfo(
        name="Stripe",
        config_file="stripe.json",
        required_env_vars=["STRIPE_API_KEY"],
        setup_instructions=(
            "1. Go to https://dashboard.stripe.com/apikeys\n"
            "2. Copy your secret key\n"
            "3. Export it: export STRIPE_API_KEY=<your-key>"
        ),
        install_hint="npx -y @stripe/mcp --tools=all",
    ),
    "supabase": MCPServiceInfo(
        name="Supabase",
        config_file="supabase.json",
        required_env_vars=["SUPABASE_URL", "SUPABASE_ANON_KEY"],
        setup_instructions=(
            "1. Go to your Supabase project settings > API\n"
            "2. Copy the Project URL and anon/public key\n"
            "3. Export them:\n"
            "   export SUPABASE_URL=<your-project-url>\n"
            "   export SUPABASE_ANON_KEY=<your-anon-key>"
        ),
        install_hint="npx -y supabase-mcp-server",
    ),
}

# Patterns for detecting service references in text.
# Uses word boundaries to avoid matching substrings (e.g. "stripey").
_SERVICE_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    for name in SERVICE_CATALOG
}


def get_mcp_config_path(service: str) -> Path | None:
    """Return the MCP config file path for a service, or None if unavailable.

    Performs a case-insensitive lookup against the SERVICE_CATALOG.
    """
    info = SERVICE_CATALOG.get(service.lower())
    if info is None:
        return None
    config_path = MCP_CONFIGS_DIR / info.config_file
    if not config_path.exists():
        return None
    return config_path


def validate_service(service: str) -> MCPValidationResult:
    """Check whether an MCP service is fully configured and ready to use."""
    service_lower = service.lower()
    info = SERVICE_CATALOG.get(service_lower)

    if info is None:
        return MCPValidationResult(
            service_name=service_lower,
            config_exists=False,
        )

    config_path = MCP_CONFIGS_DIR / info.config_file
    missing = [var for var in info.required_env_vars if not os.environ.get(var)]

    return MCPValidationResult(
        service_name=service_lower,
        config_exists=config_path.exists(),
        missing_env_vars=missing,
    )


def detect_services_from_text(text: str) -> list[str]:
    """Scan text for references to known MCP services.

    Returns a deduplicated list of lowercase service names found.
    """
    found: list[str] = []
    for name, pattern in _SERVICE_PATTERNS.items():
        if pattern.search(text):
            found.append(name)
    return found


def merge_mcp_configs(
    services: list[str],
    output_dir: Path | None = None,
) -> Path | None:
    """Merge MCP configs for multiple services into a single JSON file.

    Claude CLI accepts only one ``--mcp-config`` path, so when a sprint
    needs multiple services their ``mcpServers`` objects are merged.

    Returns the path to the merged config, or None if no valid configs found.
    """
    merged_servers: dict = {}

    for service in services:
        config_path = get_mcp_config_path(service)
        if config_path is None:
            continue
        data = json.loads(config_path.read_text())
        merged_servers.update(data.get("mcpServers", {}))

    if not merged_servers:
        return None

    merged = {"mcpServers": merged_servers}

    if output_dir is None:
        output_dir = MCP_CONFIGS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    merged_path = output_dir / "merged_mcp_config.json"
    merged_path.write_text(json.dumps(merged, indent=2) + "\n")
    return merged_path
