"""Tests for the track-based UAT dispatcher."""

import pytest

from agentic_dev.tracks import Track
from agentic_dev.uat.dispatcher import _read_desktop_framework, pick_uat_agent


class TestPickUatAgent:
    def test_web_kind(self):
        assert pick_uat_agent(Track(name="web", kind="web", uat_kind="web")) == "uat_web"

    def test_api_kind(self):
        assert pick_uat_agent(Track(name="api", kind="api", uat_kind="api")) == "uat_api"

    def test_cli_kind(self):
        assert pick_uat_agent(Track(name="cli", kind="cli", uat_kind="cli")) == "uat_cli"

    def test_mobile_kind(self):
        assert (
            pick_uat_agent(Track(name="m", kind="mobile", uat_kind="mobile")) == "uat_mobile"
        )

    def test_desktop_electron(self):
        track = Track(name="d", kind="desktop", uat_kind="desktop")
        assert pick_uat_agent(track, desktop_framework="electron") == "uat_desktop_electron"

    def test_desktop_tauri(self):
        track = Track(name="d", kind="desktop", uat_kind="desktop")
        assert pick_uat_agent(track, desktop_framework="tauri") == "uat_desktop_tauri"

    def test_desktop_requires_framework(self):
        track = Track(name="d", kind="desktop", uat_kind="desktop")
        with pytest.raises(ValueError, match="desktop_framework is required"):
            pick_uat_agent(track)

    def test_desktop_unknown_framework(self):
        track = Track(name="d", kind="desktop", uat_kind="desktop")
        with pytest.raises(ValueError, match="Unknown desktop_framework"):
            pick_uat_agent(track, desktop_framework="weird")

    def test_missing_uat_kind_raises(self):
        with pytest.raises(ValueError, match="no uat_kind"):
            pick_uat_agent(Track(name="lib", kind="library"))


class TestReadDesktopFramework:
    def test_extracts_electron(self):
        spec = "# Frontend Spec\n## desktop_framework: electron\n"
        assert _read_desktop_framework(spec) == "electron"

    def test_extracts_tauri(self):
        spec = "# Frontend Spec\n## desktop_framework\ntauri\n"
        assert _read_desktop_framework(spec) == "tauri"

    def test_returns_none_for_unknown(self):
        spec = "## desktop_framework: weird\n"
        assert _read_desktop_framework(spec) is None

    def test_returns_none_when_absent(self):
        assert _read_desktop_framework("# Frontend Spec\n## Pages") is None
