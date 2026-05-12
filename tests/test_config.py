"""Tests for ProjectConfig, config migration, and project registry."""

import json

import pytest

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    CONFIG_FILE,
    ExternalSource,
    ProjectConfig,
    load_project_config,
    load_registry,
    register_project,
    resolve_project_path,
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


class TestProjectRegistry:
    def test_load_empty_registry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", tmp_path / "registry.json")
        assert load_registry() == {}

    def test_register_and_load(self, tmp_path, monkeypatch):
        registry_file = tmp_path / "registry.json"
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", registry_file)
        monkeypatch.setattr("agentic_dev.config.GLOBAL_REGISTRY_DIR", tmp_path)

        register_project("my-app", tmp_path / "my-app")
        registry = load_registry()
        assert "my-app" in registry
        assert registry["my-app"] == str((tmp_path / "my-app").resolve())

    def test_resolve_from_registry(self, tmp_path, monkeypatch):
        registry_file = tmp_path / "registry.json"
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", registry_file)
        monkeypatch.setattr("agentic_dev.config.GLOBAL_REGISTRY_DIR", tmp_path)

        project_path = tmp_path / "custom" / "location"
        register_project("my-app", project_path)

        resolved = resolve_project_path("my-app")
        assert resolved == project_path.resolve()

    def test_resolve_falls_back_to_base_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", tmp_path / "registry.json")

        resolved = resolve_project_path("unknown-app", base_dir=tmp_path)
        assert resolved == tmp_path / "unknown-app"

    def test_register_overwrites_existing(self, tmp_path, monkeypatch):
        registry_file = tmp_path / "registry.json"
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", registry_file)
        monkeypatch.setattr("agentic_dev.config.GLOBAL_REGISTRY_DIR", tmp_path)

        register_project("my-app", tmp_path / "old-path")
        register_project("my-app", tmp_path / "new-path")
        registry = load_registry()
        assert registry["my-app"] == str((tmp_path / "new-path").resolve())
