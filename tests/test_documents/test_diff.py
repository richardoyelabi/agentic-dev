"""Tests for document diffing logic."""

from agentic_dev.documents.diff import (
    DiffResult,
    determine_restart_phase,
    diff_structured_input,
)


class TestDiffStructuredInput:
    def test_detect_added_features(self):
        old = "- [F001] Login: User authentication"
        new = (
            "- [F001] Login: User authentication\n"
            "- [F002] Dashboard: Main dashboard view"
        )

        result = diff_structured_input(old, new)
        assert result.added_features == ["F002"]
        assert result.modified_features == []
        assert result.removed_features == []

    def test_detect_removed_features(self):
        old = (
            "- [F001] Login: User authentication\n"
            "- [F002] Dashboard: Main dashboard view"
        )
        new = "- [F001] Login: User authentication"

        result = diff_structured_input(old, new)
        assert result.added_features == []
        assert result.removed_features == ["F002"]

    def test_detect_modified_features(self):
        old = "- [F001] Login: Basic authentication"
        new = "- [F001] Login: OAuth2 authentication with SSO"

        result = diff_structured_input(old, new)
        assert result.modified_features == ["F001"]
        assert result.added_features == []
        assert result.removed_features == []

    def test_no_changes(self):
        text = "- [F001] Login: User authentication"
        result = diff_structured_input(text, text)
        assert result.added_features == []
        assert result.modified_features == []
        assert result.removed_features == []

    def test_restart_from_feature_analysis_on_added(self):
        old = "- [F001] Login"
        new = "- [F001] Login\n- [F002] Signup"
        result = diff_structured_input(old, new)
        assert result.restart_from == "feature_analysis"

    def test_restart_from_feature_analysis_on_modified(self):
        old = "- [F001] Login: basic"
        new = "- [F001] Login: advanced"
        result = diff_structured_input(old, new)
        assert result.restart_from == "feature_analysis"

    def test_restart_from_architecture_on_ui_only_change(self):
        old = (
            "- [F001] Login: User authentication\n"
            "### UI/UX Preferences\n"
            "- Dark theme"
        )
        new = (
            "- [F001] Login: User authentication\n"
            "### UI/UX Preferences\n"
            "- Light theme"
        )
        result = diff_structured_input(old, new)
        assert result.restart_from == "architecture"


class TestDetermineRestartPhase:
    def test_added_features_restart_from_feature_analysis(self):
        diff = DiffResult(
            added_features=["F002"],
            modified_features=[],
            removed_features=[],
            restart_from="",
        )
        assert determine_restart_phase(diff) == "feature_analysis"

    def test_modified_features_restart_from_feature_analysis(self):
        diff = DiffResult(
            added_features=[],
            modified_features=["F001"],
            removed_features=[],
            restart_from="",
        )
        assert determine_restart_phase(diff) == "feature_analysis"

    def test_removed_features_restart_from_feature_analysis(self):
        diff = DiffResult(
            added_features=[],
            modified_features=[],
            removed_features=["F003"],
            restart_from="",
        )
        assert determine_restart_phase(diff) == "feature_analysis"

    def test_content_changed_no_feature_changes_restart_from_architecture(self):
        diff = DiffResult(
            added_features=[],
            modified_features=[],
            removed_features=[],
            restart_from="",
        )
        assert determine_restart_phase(diff, content_changed=True) == "architecture"

    def test_no_changes_defaults_to_feature_analysis(self):
        diff = DiffResult(
            added_features=[],
            modified_features=[],
            removed_features=[],
            restart_from="",
        )
        assert determine_restart_phase(diff) == "feature_analysis"
