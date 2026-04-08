"""Tests for Claude Code MCP settings discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_dev.mcp.claude_settings import (
    ClaudeMCPEnvironment,
    MCPServerEntry,
    discover_mcp_servers,
    find_server_for_service,
)


def _write_settings(path: Path, mcp_servers: dict) -> None:
    """Helper to write a Claude Code settings file with mcpServers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": mcp_servers}, indent=2))


class TestMCPServerEntry:
    """Tests for the MCPServerEntry model."""

    def test_stdio_server(self) -> None:
        entry = MCPServerEntry(
            name="figma",
            transport="stdio",
            source="global",
        )
        assert entry.name == "figma"
        assert entry.transport == "stdio"
        assert entry.source == "global"

    def test_sse_server(self) -> None:
        entry = MCPServerEntry(
            name="slack",
            transport="sse",
            source="project",
        )
        assert entry.transport == "sse"
        assert entry.source == "project"

    def test_http_server(self) -> None:
        entry = MCPServerEntry(
            name="github",
            transport="http",
            source="plugin",
        )
        assert entry.transport == "http"


class TestClaudeMCPEnvironment:
    """Tests for the ClaudeMCPEnvironment model."""

    def test_empty_environment(self) -> None:
        env = ClaudeMCPEnvironment(servers={})
        assert env.available_server_names == set()
        assert env.has_server("figma") is False

    def test_has_server(self) -> None:
        env = ClaudeMCPEnvironment(
            servers={"figma": MCPServerEntry(name="figma", transport="stdio", source="global")}
        )
        assert env.has_server("figma") is True
        assert env.has_server("github") is False

    def test_available_server_names(self) -> None:
        env = ClaudeMCPEnvironment(
            servers={
                "figma": MCPServerEntry(name="figma", transport="stdio", source="global"),
                "github": MCPServerEntry(name="github", transport="http", source="global"),
            }
        )
        assert env.available_server_names == {"figma", "github"}


class TestDiscoverMCPServers:
    """Tests for discover_mcp_servers()."""

    def test_empty_when_no_settings_exist(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.servers == {}

    def test_reads_global_settings(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "figma": {
                "command": "npx",
                "args": ["-y", "@anthropic-ai/figma-mcp-server"],
            }
        })
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.has_server("figma")
        assert env.servers["figma"].source == "global"
        assert env.servers["figma"].transport == "stdio"

    def test_reads_project_settings(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        project_dir = tmp_path / "project"
        _write_settings(project_dir / ".claude" / "settings.json", {
            "supabase": {
                "command": "npx",
                "args": ["-y", "supabase-mcp-server"],
            }
        })
        env = discover_mcp_servers(project_dir=project_dir, claude_home=claude_home)
        assert env.has_server("supabase")
        assert env.servers["supabase"].source == "project"

    def test_reads_project_local_settings(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        project_dir = tmp_path / "project"
        _write_settings(project_dir / ".claude" / "settings.local.json", {
            "stripe": {
                "command": "npx",
                "args": ["-y", "@stripe/mcp"],
            }
        })
        env = discover_mcp_servers(project_dir=project_dir, claude_home=claude_home)
        assert env.has_server("stripe")
        assert env.servers["stripe"].source == "project-local"

    def test_project_settings_override_global(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "project"
        _write_settings(claude_home / "settings.json", {
            "figma": {
                "command": "npx",
                "args": ["-y", "@anthropic-ai/figma-mcp-server"],
            }
        })
        _write_settings(project_dir / ".claude" / "settings.json", {
            "figma": {
                "type": "sse",
                "url": "https://custom-figma.example.com/sse",
            }
        })
        env = discover_mcp_servers(project_dir=project_dir, claude_home=claude_home)
        assert env.servers["figma"].source == "project"
        assert env.servers["figma"].transport == "sse"

    def test_local_overrides_project(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        project_dir = tmp_path / "project"
        _write_settings(project_dir / ".claude" / "settings.json", {
            "figma": {"command": "npx", "args": ["-y", "@anthropic-ai/figma-mcp-server"]}
        })
        _write_settings(project_dir / ".claude" / "settings.local.json", {
            "figma": {"type": "http", "url": "https://local.example.com/mcp"}
        })
        env = discover_mcp_servers(project_dir=project_dir, claude_home=claude_home)
        assert env.servers["figma"].source == "project-local"
        assert env.servers["figma"].transport == "http"

    def test_merges_servers_from_all_sources(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "project"
        _write_settings(claude_home / "settings.json", {
            "figma": {"command": "npx", "args": ["-y", "@anthropic-ai/figma-mcp-server"]}
        })
        _write_settings(project_dir / ".claude" / "settings.json", {
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}
        })
        _write_settings(project_dir / ".claude" / "settings.local.json", {
            "stripe": {"command": "npx", "args": ["-y", "@stripe/mcp"]}
        })
        env = discover_mcp_servers(project_dir=project_dir, claude_home=claude_home)
        assert env.available_server_names == {"figma", "github", "stripe"}

    def test_detects_sse_transport(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "slack": {"type": "sse", "url": "https://mcp.slack.com/sse"}
        })
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.servers["slack"].transport == "sse"

    def test_detects_http_transport(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        _write_settings(claude_home / "settings.json", {
            "github": {
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"Authorization": "Bearer ${TOKEN}"},
            }
        })
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.servers["github"].transport == "http"

    def test_handles_malformed_settings_gracefully(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        settings = claude_home / "settings.json"
        settings.write_text("not valid json {{{")
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.servers == {}

    def test_handles_settings_without_mcp_servers_key(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        settings = claude_home / "settings.json"
        settings.write_text(json.dumps({"someOtherKey": True}))
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.servers == {}

    def test_discovers_external_plugin(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        settings = claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"github@my-marketplace": True}
        }))
        ext_dir = claude_home / "plugins" / "marketplaces" / "my-marketplace" / "external_plugins" / "github"
        ext_dir.mkdir(parents=True)
        (ext_dir / ".mcp.json").write_text(json.dumps({
            "github": {
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
            }
        }))
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.has_server("github")
        assert env.servers["github"].source == "plugin"
        assert env.servers["github"].transport == "http"

    def test_discovers_cached_plugin_with_server_json(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        settings = claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"figma@claude-plugins-official": True}
        }))
        cache_dir = claude_home / "plugins" / "cache" / "claude-plugins-official" / "figma" / "2.0.7"
        cache_dir.mkdir(parents=True)
        (cache_dir / "server.json").write_text(json.dumps({
            "name": "com.figma.mcp/mcp",
            "remotes": [{"type": "streamable-http", "url": "https://mcp.figma.com/mcp"}]
        }))
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.has_server("figma")
        assert env.servers["figma"].source == "plugin"
        assert env.servers["figma"].transport == "streamable-http"

    def test_discovers_cached_plugin_with_mcp_json(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        settings = claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"omc@omc-marketplace": True}
        }))
        cache_dir = claude_home / "plugins" / "cache" / "omc-marketplace" / "omc" / "1.0.0"
        cache_dir.mkdir(parents=True)
        (cache_dir / ".mcp.json").write_text(json.dumps({
            "mcpServers": {
                "t": {"command": "node", "args": ["server.cjs"]}
            }
        }))
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.has_server("t")
        assert env.servers["t"].transport == "stdio"

    def test_skips_disabled_plugins(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        settings = claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"github@my-marketplace": False}
        }))
        ext_dir = claude_home / "plugins" / "marketplaces" / "my-marketplace" / "external_plugins" / "github"
        ext_dir.mkdir(parents=True)
        (ext_dir / ".mcp.json").write_text(json.dumps({
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}
        }))
        env = discover_mcp_servers(claude_home=claude_home)
        assert not env.has_server("github")

    def test_combines_settings_and_plugins(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        settings = claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "mcpServers": {
                "custom-server": {"command": "node", "args": ["server.js"]}
            },
            "enabledPlugins": {"github@mp": True}
        }))
        ext_dir = claude_home / "plugins" / "marketplaces" / "mp" / "external_plugins" / "github"
        ext_dir.mkdir(parents=True)
        (ext_dir / ".mcp.json").write_text(json.dumps({
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}
        }))
        env = discover_mcp_servers(claude_home=claude_home)
        assert env.has_server("custom-server")
        assert env.has_server("github")


class TestFindServerForService:
    """Tests for find_server_for_service()."""

    def _env_with(self, servers: dict[str, dict]) -> ClaudeMCPEnvironment:
        """Create an environment from a simplified server dict."""
        entries = {}
        for name, config in servers.items():
            transport = config.get("type", "stdio")
            entries[name] = MCPServerEntry(name=name, transport=transport, source="global")
        return ClaudeMCPEnvironment(servers=entries)

    def test_exact_name_match(self) -> None:
        env = self._env_with({"figma": {}})
        result = find_server_for_service(env, "figma")
        assert result is not None
        assert result.name == "figma"

    def test_case_insensitive_match(self) -> None:
        env = self._env_with({"Figma": {}})
        result = find_server_for_service(env, "figma")
        assert result is not None

    def test_substring_match(self) -> None:
        env = self._env_with({"figma-remote-mcp": {}})
        result = find_server_for_service(env, "figma")
        assert result is not None
        assert result.name == "figma-remote-mcp"

    def test_no_match_returns_none(self) -> None:
        env = self._env_with({"github": {}})
        result = find_server_for_service(env, "figma")
        assert result is None

    def test_empty_environment(self) -> None:
        env = ClaudeMCPEnvironment(servers={})
        result = find_server_for_service(env, "figma")
        assert result is None

    def test_prefers_exact_match_over_substring(self) -> None:
        env = self._env_with({"figma": {}, "figma-remote-mcp": {}})
        result = find_server_for_service(env, "figma")
        assert result is not None
        assert result.name == "figma"

    def test_matches_plugin_prefixed_names(self) -> None:
        env = self._env_with({"plugin:figma:figma": {}})
        result = find_server_for_service(env, "figma")
        assert result is not None

    def test_matches_github_variations(self) -> None:
        env = self._env_with({"my-github-server": {}})
        result = find_server_for_service(env, "github")
        assert result is not None

    def test_does_not_false_match(self) -> None:
        env = self._env_with({"stripe": {}})
        result = find_server_for_service(env, "trip")
        assert result is None
