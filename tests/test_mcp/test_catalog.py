"""Tests for the MCP service catalog."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agentic_dev.mcp.catalog import (
    MCPServiceInfo,
    MCPValidationResult,
    SERVICE_CATALOG,
    detect_services_from_text,
    get_mcp_config_path,
    merge_mcp_configs,
    validate_service,
)


class TestMCPServiceInfo:
    """Tests for the MCPServiceInfo model."""

    def test_service_info_fields(self) -> None:
        info = MCPServiceInfo(
            name="test",
            config_file="test.json",
            required_env_vars=["TEST_KEY"],
            setup_instructions="Set TEST_KEY.",
            install_hint="npx -y @test/mcp-server",
        )
        assert info.name == "test"
        assert info.config_file == "test.json"
        assert info.required_env_vars == ["TEST_KEY"]
        assert info.setup_instructions == "Set TEST_KEY."
        assert info.install_hint == "npx -y @test/mcp-server"


class TestServiceCatalog:
    """Tests for the SERVICE_CATALOG registry."""

    def test_catalog_contains_figma(self) -> None:
        assert "figma" in SERVICE_CATALOG

    def test_catalog_contains_github(self) -> None:
        assert "github" in SERVICE_CATALOG

    def test_catalog_contains_stripe(self) -> None:
        assert "stripe" in SERVICE_CATALOG

    def test_catalog_contains_supabase(self) -> None:
        assert "supabase" in SERVICE_CATALOG

    def test_figma_requires_access_token(self) -> None:
        assert "FIGMA_ACCESS_TOKEN" in SERVICE_CATALOG["figma"].required_env_vars

    def test_github_requires_token(self) -> None:
        assert "GITHUB_TOKEN" in SERVICE_CATALOG["github"].required_env_vars

    def test_stripe_requires_api_key(self) -> None:
        assert "STRIPE_API_KEY" in SERVICE_CATALOG["stripe"].required_env_vars

    def test_supabase_requires_url_and_key(self) -> None:
        env_vars = SERVICE_CATALOG["supabase"].required_env_vars
        assert "SUPABASE_URL" in env_vars
        assert "SUPABASE_ANON_KEY" in env_vars


class TestGetMcpConfigPath:
    """Tests for get_mcp_config_path()."""

    def test_returns_path_for_known_service_with_config(self) -> None:
        path = get_mcp_config_path("figma")
        assert path is not None
        assert path.name == "figma.json"
        assert path.exists()

    def test_returns_none_for_unknown_service(self) -> None:
        assert get_mcp_config_path("nonexistent_service") is None

    def test_returns_none_for_missing_config_file(self) -> None:
        with patch.dict(SERVICE_CATALOG, {"fake": MCPServiceInfo(
            name="fake",
            config_file="does_not_exist.json",
            required_env_vars=[],
            setup_instructions="",
            install_hint="",
        )}):
            assert get_mcp_config_path("fake") is None

    def test_case_insensitive_lookup(self) -> None:
        path = get_mcp_config_path("Figma")
        assert path is not None
        assert path.name == "figma.json"


class TestValidateService:
    """Tests for validate_service()."""

    def test_valid_service_with_env_vars_set(self) -> None:
        with patch.dict(os.environ, {"FIGMA_ACCESS_TOKEN": "test-token"}):
            result = validate_service("figma")
        assert result.config_exists is True
        assert result.missing_env_vars == []
        assert result.is_ready is True

    def test_valid_service_with_missing_env_vars(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("FIGMA_ACCESS_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                result = validate_service("figma")
        assert result.config_exists is True
        assert "FIGMA_ACCESS_TOKEN" in result.missing_env_vars
        assert result.is_ready is False

    def test_unknown_service_returns_not_ready(self) -> None:
        result = validate_service("unknown_service")
        assert result.config_exists is False
        assert result.is_ready is False

    def test_service_name_preserved_in_result(self) -> None:
        result = validate_service("github")
        assert result.service_name == "github"

    def test_supabase_partially_configured(self) -> None:
        with patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co"}, clear=False):
            env = os.environ.copy()
            env.pop("SUPABASE_ANON_KEY", None)
            env["SUPABASE_URL"] = "https://test.supabase.co"
            with patch.dict(os.environ, env, clear=True):
                result = validate_service("supabase")
        assert result.config_exists is True
        assert "SUPABASE_ANON_KEY" in result.missing_env_vars
        assert "SUPABASE_URL" not in result.missing_env_vars
        assert result.is_ready is False


class TestDetectServicesFromText:
    """Tests for detect_services_from_text()."""

    def test_detects_stripe_in_text(self) -> None:
        text = "This sprint integrates Stripe for payment processing."
        services = detect_services_from_text(text)
        assert "stripe" in services

    def test_detects_multiple_services(self) -> None:
        text = "Connect GitHub for auth and Supabase for the database."
        services = detect_services_from_text(text)
        assert "github" in services
        assert "supabase" in services

    def test_detects_figma(self) -> None:
        text = "Import designs from Figma to match the mockups."
        services = detect_services_from_text(text)
        assert "figma" in services

    def test_returns_empty_for_no_services(self) -> None:
        text = "This sprint implements the core data models."
        services = detect_services_from_text(text)
        assert services == []

    def test_case_insensitive_detection(self) -> None:
        text = "Use STRIPE for payments and GITHUB for version control."
        services = detect_services_from_text(text)
        assert "stripe" in services
        assert "github" in services

    def test_no_duplicates(self) -> None:
        text = "Stripe integration. Also configure Stripe webhooks."
        services = detect_services_from_text(text)
        assert services.count("stripe") == 1

    def test_does_not_match_substrings(self) -> None:
        text = "The stripey pattern on the background."
        services = detect_services_from_text(text)
        assert "stripe" not in services


class TestMergeMcpConfigs:
    """Tests for merge_mcp_configs()."""

    def test_merge_single_service(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs(["figma"], output_dir=tmp_path)
        assert merged is not None
        assert merged.exists()
        data = json.loads(merged.read_text())
        assert "figma" in data["mcpServers"]

    def test_merge_multiple_services(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs(["github", "stripe"], output_dir=tmp_path)
        assert merged is not None
        data = json.loads(merged.read_text())
        assert "github" in data["mcpServers"]
        assert "stripe" in data["mcpServers"]

    def test_merge_skips_unknown_services(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs(["github", "nonexistent"], output_dir=tmp_path)
        assert merged is not None
        data = json.loads(merged.read_text())
        assert "github" in data["mcpServers"]
        assert len(data["mcpServers"]) == 1

    def test_merge_returns_none_for_all_unknown(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs(["nonexistent1", "nonexistent2"], output_dir=tmp_path)
        assert merged is None

    def test_merge_returns_none_for_empty_list(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs([], output_dir=tmp_path)
        assert merged is None

    def test_merged_file_is_valid_json(self, tmp_path: Path) -> None:
        merged = merge_mcp_configs(["figma", "github", "stripe"], output_dir=tmp_path)
        assert merged is not None
        data = json.loads(merged.read_text())
        assert len(data["mcpServers"]) == 3
