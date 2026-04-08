"""Tests for the MCP service detection."""

from __future__ import annotations

from agentic_dev.mcp.catalog import KNOWN_SERVICES, detect_services_from_text


class TestKnownServices:
    """Tests for the KNOWN_SERVICES list."""

    def test_contains_expected_services(self) -> None:
        assert "figma" in KNOWN_SERVICES
        assert "github" in KNOWN_SERVICES
        assert "stripe" in KNOWN_SERVICES
        assert "supabase" in KNOWN_SERVICES


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
