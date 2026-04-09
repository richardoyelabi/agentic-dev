"""Discover MCP servers from Claude Code's native settings files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_HOME = Path.home() / ".claude"


class MCPServerEntry(BaseModel):
    """A single MCP server discovered in Claude Code settings."""

    name: str
    transport: str  # stdio, sse, http, streamable-http, ws
    source: str  # global, project, project-local, plugin


class ClaudeMCPEnvironment(BaseModel):
    """Aggregated view of all MCP servers available in Claude Code."""

    servers: dict[str, MCPServerEntry] = {}

    @property
    def available_server_names(self) -> set[str]:
        """Return the set of all configured server names."""
        return set(self.servers.keys())

    def has_server(self, name: str) -> bool:
        """Check if a server with the given name is configured."""
        return name in self.servers


def _infer_transport(server_config: dict) -> str:
    """Infer the transport type from a server config entry."""
    explicit = server_config.get("type", "").lower()
    if explicit in ("sse", "http", "ws", "websocket"):
        return "http" if explicit == "http" else explicit
    if "url" in server_config and not server_config.get("command"):
        url = server_config["url"]
        if "sse" in url.lower() or explicit == "sse":
            return "sse"
        if url.startswith("wss://") or url.startswith("ws://"):
            return "ws"
        return "http"
    return "stdio"


def _read_settings_file(path: Path, source: str) -> dict[str, MCPServerEntry]:
    """Parse a Claude Code settings file and extract mcpServers entries."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse Claude Code settings at %s", path)
        return {}

    mcp_servers = data.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return {}

    entries: dict[str, MCPServerEntry] = {}
    for name, config in mcp_servers.items():
        if not isinstance(config, dict):
            continue
        entries[name] = MCPServerEntry(
            name=name,
            transport=_infer_transport(config),
            source=source,
        )
    return entries


def _read_enabled_plugins(settings_path: Path) -> set[str]:
    """Read the enabledPlugins from a settings file.

    Returns a set of ``"plugin@marketplace"`` strings for enabled plugins.
    """
    if not settings_path.exists():
        return set()
    try:
        data = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    enabled = data.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        return set()
    return {key for key, val in enabled.items() if val is True}


def _read_mcp_json(path: Path, source: str) -> dict[str, MCPServerEntry]:
    """Parse a plugin ``.mcp.json`` file.

    Handles both formats:
    - Wrapped: ``{"mcpServers": {"name": {...}}}``
    - Flat: ``{"name": {...}}``
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse MCP config at %s", path)
        return {}

    if not isinstance(data, dict):
        return {}

    # Wrapped format
    if "mcpServers" in data and isinstance(data["mcpServers"], dict):
        servers = data["mcpServers"]
    else:
        servers = data

    entries: dict[str, MCPServerEntry] = {}
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        # Skip JSON schema keys
        if name.startswith("$"):
            continue
        entries[name] = MCPServerEntry(
            name=name,
            transport=_infer_transport(config),
            source=source,
        )
    return entries


def _read_server_json(path: Path, plugin_name: str) -> dict[str, MCPServerEntry]:
    """Parse a plugin ``server.json`` file (MCPB format).

    These use a ``remotes`` array with transport types like ``streamable-http``.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse server.json at %s", path)
        return {}

    if not isinstance(data, dict):
        return {}

    remotes = data.get("remotes", [])
    if not remotes:
        return {}

    remote = remotes[0] if isinstance(remotes, list) else {}
    transport = remote.get("type", "http") if isinstance(remote, dict) else "http"

    return {
        plugin_name: MCPServerEntry(
            name=plugin_name,
            transport=transport,
            source="plugin",
        )
    }


def _discover_plugin_servers(
    claude_home: Path,
    enabled_plugins: set[str],
) -> dict[str, MCPServerEntry]:
    """Discover MCP servers from enabled Claude Code plugins.

    Scans:
    1. External plugins: ``plugins/marketplaces/<marketplace>/external_plugins/<name>/.mcp.json``
    2. Cached plugins: ``plugins/cache/<marketplace>/<plugin>/<version>/server.json``
       and ``plugins/cache/<marketplace>/<plugin>/<version>/.mcp.json``
    """
    servers: dict[str, MCPServerEntry] = {}
    plugins_dir = claude_home / "plugins"

    if not plugins_dir.exists():
        return servers

    for plugin_key in enabled_plugins:
        if "@" not in plugin_key:
            continue
        plugin_name, marketplace = plugin_key.split("@", 1)

        # Check external plugins
        ext_mcp = (
            plugins_dir / "marketplaces" / marketplace
            / "external_plugins" / plugin_name / ".mcp.json"
        )
        if ext_mcp.exists():
            servers.update(_read_mcp_json(ext_mcp, "plugin"))
            continue

        # Check cached plugins (versioned directories)
        cache_dir = plugins_dir / "cache" / marketplace / plugin_name
        if cache_dir.exists():
            versions = sorted(
                [d for d in cache_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            if versions:
                latest = versions[0]
                server_json = latest / "server.json"
                mcp_json = latest / ".mcp.json"
                if server_json.exists():
                    servers.update(_read_server_json(server_json, plugin_name))
                elif mcp_json.exists():
                    servers.update(_read_mcp_json(mcp_json, "plugin"))

    return servers


def discover_mcp_servers(
    project_dir: Path | None = None,
    claude_home: Path | None = None,
) -> ClaudeMCPEnvironment:
    """Discover all MCP servers configured in Claude Code.

    Reads from (in precedence order, later overrides earlier):
    1. ``~/.claude.json`` (user config ``mcpServers``)
    2. ``~/.claude/settings.json`` (global ``mcpServers``)
    3. ``<project>/.claude/settings.json`` (project ``mcpServers``)
    4. ``<project>/.claude/settings.local.json`` (project-local ``mcpServers``)
    5. Enabled plugins (external and cached plugin MCP servers)

    Args:
        project_dir: Project directory to check for project-level settings.
        claude_home: Path to the Claude home directory (defaults to ``~/.claude``).

    Returns:
        A ``ClaudeMCPEnvironment`` containing all discovered servers.
    """
    if claude_home is None:
        claude_home = DEFAULT_CLAUDE_HOME

    servers: dict[str, MCPServerEntry] = {}

    # 1. User config (~/.claude.json — servers added via `claude mcp add`)
    servers.update(
        _read_settings_file(claude_home.parent / ".claude.json", "user-config")
    )

    # 2. Global settings (mcpServers)
    servers.update(_read_settings_file(claude_home / "settings.json", "global"))

    # 2. Project settings
    if project_dir is not None:
        servers.update(
            _read_settings_file(project_dir / ".claude" / "settings.json", "project")
        )
        # 3. Project-local settings
        servers.update(
            _read_settings_file(
                project_dir / ".claude" / "settings.local.json", "project-local"
            )
        )

    # 4. Enabled plugins
    enabled_plugins = _read_enabled_plugins(claude_home / "settings.json")
    servers.update(_discover_plugin_servers(claude_home, enabled_plugins))

    return ClaudeMCPEnvironment(servers=servers)


def find_server_for_service(
    env: ClaudeMCPEnvironment,
    service_name: str,
) -> MCPServerEntry | None:
    """Find an MCP server matching a service name using fuzzy matching.

    Matching strategy (in priority order):
    1. Exact name match (case-insensitive)
    2. Server name contains the service name (case-insensitive)

    Args:
        env: The discovered MCP environment.
        service_name: The service to find (e.g. "figma", "github").

    Returns:
        The matching ``MCPServerEntry``, or ``None`` if no match found.
    """
    lower_service = service_name.lower()

    # 1. Exact match (case-insensitive)
    for name, entry in env.servers.items():
        if name.lower() == lower_service:
            return entry

    # 2. Segment match (service name appears as a whole segment separated by
    # common delimiters like hyphens, underscores, colons, or dots)
    segment_pattern = re.compile(
        rf"(?:^|[-_:.])({re.escape(lower_service)})(?:[-_:.]|$)"
    )
    for name, entry in env.servers.items():
        if segment_pattern.search(name.lower()):
            return entry

    return None
