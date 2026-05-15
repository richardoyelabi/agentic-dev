"""Tests for ProjectConfig, config migration, and cwd-based project resolution."""

import json

import pytest

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    CONFIG_FILE,
    ExternalSource,
    ProjectConfig,
    load_project_config,
    resolve_project_dir,
    save_project_config,
)
from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.tracks import Track


class TestExternalSource:
    def test_defaults(self):
        src = ExternalSource(value="/path/to/code")
        assert src.value == "/path/to/code"
        assert src.annotation == ""

    def test_with_annotation(self):
        src = ExternalSource(value="https://figma.com/file/abc", annotation="Main UI")
        assert src.annotation == "Main UI"


class TestProjectConfig:
    def test_minimal_creation(self):
        config = ProjectConfig(app_name="my-app")
        assert config.app_name == "my-app"
        # Default is a single ``app`` track.
        assert len(config.tracks) == 1
        assert config.tracks[0].name == "app"
        assert config.sources == {}
        assert config.checkpoint == CheckpointConfig()
        assert config.uat_mode == "full"

    def test_full_creation(self):
        config = ProjectConfig(
            app_name="my-app",
            tracks=[
                Track(name="web", path="web", kind="web", uat_kind="web"),
                Track(name="api", path="api", kind="api", uat_kind="api"),
            ],
            sources={
                "codebases": [ExternalSource(value="/path", annotation="Frontend")],
                "figma": [ExternalSource(value="https://figma.com/file/abc")],
            },
            checkpoint=CheckpointConfig(after_design=False),
            uat_mode="spec_only",
        )
        assert {t.name for t in config.tracks} == {"web", "api"}
        assert config.uat_mode == "spec_only"
        assert config.checkpoint.after_design is False

    def test_invalid_uat_mode_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProjectConfig(app_name="my-app", uat_mode="something_else")

    def test_serialization_roundtrip(self):
        config = ProjectConfig(
            app_name="test",
            tracks=[Track(name="app", path=".", kind="generic")],
        )
        data = config.model_dump()
        restored = ProjectConfig.model_validate(data)
        assert restored == config


class TestConfigMigration:
    def test_load_old_format_migrates_checkpoint(self, tmp_path):
        """Old flat CheckpointConfig format should be wrapped into ProjectConfig."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "after_design": False,
            "after_each_sprint": True,
            "before_uat": False,
        }))

        config = load_project_config(project_dir)
        assert isinstance(config, ProjectConfig)
        assert config.checkpoint.after_design is False
        assert config.checkpoint.after_each_sprint is True
        assert config.checkpoint.before_uat is False
        assert config.app_name == "my-app"

    def test_load_legacy_directory_map_is_dropped(self, tmp_path):
        """Legacy ``directory_map``/``frontend_kind``/``sync_ignores`` fields are silently dropped."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "checkpoint": {"after_design": True, "after_each_sprint": False, "before_uat": False},
            "directory_map": {"frontend": "client", "backend": "server", "root": "."},
            "frontend_kind": "web",
            "sync_ignores": ["DRIFT-1"],
        }))

        config = load_project_config(project_dir)
        assert config.app_name == "my-app"
        # Defaults used because tracks were not declared in the legacy config.
        assert config.tracks[0].name == "app"

    def test_load_missing_config_returns_defaults(self, tmp_path):
        config = load_project_config(tmp_path / "nonexistent")
        assert config.app_name == "nonexistent"
        assert config.tracks[0].name == "app"

    def test_save_and_reload_roundtrip(self, tmp_path):
        project_dir = tmp_path / "my-app"
        (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)

        original = ProjectConfig(
            app_name="my-app",
            tracks=[Track(name="web", path="web", kind="web", uat_kind="web")],
        )
        save_project_config(project_dir, original)
        restored = load_project_config(project_dir)
        assert restored == original


class TestResolveProjectDir:
    """`resolve_project_dir(cwd)` walks upward looking for ``.agentic-dev/``.

    Mirrors how ``git rev-parse --show-toplevel`` finds the repo root. When no
    metadata directory exists anywhere above cwd, cwd itself is returned as
    the prospective project root (it would be scaffolded on the first
    ``agentic-dev work`` invocation).
    """

    def test_returns_cwd_when_no_agentic_dev_anywhere(self, tmp_path):
        sub = tmp_path / "fresh-project"
        sub.mkdir()
        assert resolve_project_dir(sub) == sub

    def test_finds_agentic_dev_at_cwd(self, tmp_path):
        project = tmp_path / "my-app"
        (project / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)
        assert resolve_project_dir(project) == project

    def test_walks_up_to_find_agentic_dev_in_parent(self, tmp_path):
        project = tmp_path / "my-app"
        (project / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)
        inner = project / "backend" / "src"
        inner.mkdir(parents=True)
        assert resolve_project_dir(inner) == project

    def test_walks_up_multiple_levels(self, tmp_path):
        project = tmp_path / "deep" / "nested" / "project"
        (project / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)
        deep = project / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        assert resolve_project_dir(deep) == project

    def test_returns_cwd_when_filesystem_root_reached(self, tmp_path):
        # No .agentic-dev/ anywhere in tmp_path's ancestor chain.
        sub = tmp_path / "lonely" / "leaf"
        sub.mkdir(parents=True)
        assert resolve_project_dir(sub) == sub
