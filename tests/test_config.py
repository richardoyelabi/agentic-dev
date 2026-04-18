"""Tests for ProjectConfig, DirectoryMap, config migration, and project registry."""

import json

import pytest

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    CONFIG_FILE,
    DirectoryMap,
    ExternalSource,
    ProjectConfig,
    load_project_config,
    load_registry,
    register_project,
    resolve_project_path,
    save_project_config,
)
from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.state.models import FrontendKind


class TestDirectoryMap:
    """Tests for the DirectoryMap model."""

    def test_defaults(self):
        dm = DirectoryMap()
        assert dm.frontend is None
        assert dm.backend is None
        assert dm.root == "."

    def test_custom_paths(self):
        dm = DirectoryMap(frontend="client", backend="server")
        assert dm.frontend == "client"
        assert dm.backend == "server"

    def test_serialization_roundtrip(self):
        dm = DirectoryMap(frontend="web", backend="api", root=".")
        data = dm.model_dump()
        restored = DirectoryMap.model_validate(data)
        assert restored == dm


class TestExternalSource:
    """Tests for the ExternalSource model."""

    def test_defaults(self):
        src = ExternalSource(value="/path/to/code")
        assert src.value == "/path/to/code"
        assert src.annotation == ""

    def test_with_annotation(self):
        src = ExternalSource(value="https://figma.com/file/abc", annotation="Main UI")
        assert src.annotation == "Main UI"


class TestProjectConfig:
    """Tests for the ProjectConfig model."""

    def test_minimal_creation(self):
        config = ProjectConfig(app_name="my-app")
        assert config.app_name == "my-app"
        assert config.directory_map == DirectoryMap()
        assert config.sources == {}
        assert config.checkpoint == CheckpointConfig()
        assert config.sync_ignores == []

    def test_full_creation(self):
        config = ProjectConfig(
            app_name="my-app",
            directory_map=DirectoryMap(frontend="client", backend="api"),
            sources={
                "codebases": [ExternalSource(value="/path", annotation="Frontend")],
                "figma": [ExternalSource(value="https://figma.com/file/abc")],
            },
            checkpoint=CheckpointConfig(after_design=False),
            sync_ignores=["DRIFT-001"],
        )
        assert config.directory_map.frontend == "client"
        assert len(config.sources["codebases"]) == 1
        assert config.checkpoint.after_design is False
        assert config.sync_ignores == ["DRIFT-001"]

    def test_serialization_roundtrip(self):
        config = ProjectConfig(
            app_name="test",
            directory_map=DirectoryMap(frontend="web"),
            sync_ignores=["DRIFT-002"],
        )
        data = config.model_dump()
        restored = ProjectConfig.model_validate(data)
        assert restored == config


class TestConfigMigration:
    """Tests for config.json format migration (old flat → new nested)."""

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

    def test_load_new_format(self, tmp_path):
        """New ProjectConfig format should load directly."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "checkpoint": {"after_design": True, "after_each_sprint": False, "before_uat": False},
            "directory_map": {"frontend": "client", "backend": "server", "root": "."},
        }))

        config = load_project_config(project_dir)
        assert config.app_name == "my-app"
        assert config.directory_map.frontend == "client"
        assert config.directory_map.backend == "server"

    def test_load_missing_config_returns_defaults(self, tmp_path):
        """Missing config.json should return defaults."""
        config = load_project_config(tmp_path / "nonexistent")
        assert config.app_name == "nonexistent"
        assert config.directory_map == DirectoryMap()

    def test_save_and_reload_roundtrip(self, tmp_path):
        """Saving and reloading should produce identical config."""
        project_dir = tmp_path / "my-app"
        (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)

        original = ProjectConfig(
            app_name="my-app",
            directory_map=DirectoryMap(frontend="client"),
            sync_ignores=["DRIFT-001"],
        )
        save_project_config(project_dir, original)
        restored = load_project_config(project_dir)
        assert restored == original

    def test_old_format_preserves_all_checkpoint_values(self, tmp_path):
        """Migration should not lose any checkpoint preference values."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "after_design": True,
            "after_each_sprint": True,
            "before_uat": True,
        }))

        config = load_project_config(project_dir)
        assert config.checkpoint.after_design is True
        assert config.checkpoint.after_each_sprint is True
        assert config.checkpoint.before_uat is True


class TestProjectConfigFrontendKind:
    """Tests for frontend_kind and uat_mode on ProjectConfig + migration."""

    def test_defaults_frontend_kind_none_uat_mode_full(self):
        config = ProjectConfig(app_name="my-app")
        assert config.frontend_kind is None
        assert config.uat_mode == "full"

    def test_set_frontend_kind_and_uat_mode_explicitly(self):
        config = ProjectConfig(
            app_name="my-app",
            frontend_kind=FrontendKind.CLI,
            uat_mode="spec_only",
        )
        assert config.frontend_kind == FrontendKind.CLI
        assert config.uat_mode == "spec_only"

    def test_invalid_uat_mode_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProjectConfig(app_name="my-app", uat_mode="something_else")

    def test_load_config_without_new_fields_uses_defaults(self, tmp_path):
        """Old config without frontend_kind or uat_mode loads with defaults."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "checkpoint": {
                "after_design": True,
                "after_each_sprint": False,
                "before_uat": False,
            },
        }))

        config = load_project_config(project_dir)
        assert config.uat_mode == "full"

    def test_migration_defaults_frontend_kind_to_web_when_frontend_dir_set(
        self, tmp_path
    ):
        """Existing project with a frontend dir but no frontend_kind migrates to web."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "directory_map": {"frontend": "client", "backend": "server"},
        }))

        config = load_project_config(project_dir)
        assert config.frontend_kind == FrontendKind.WEB

    def test_migration_leaves_frontend_kind_none_when_no_frontend_dir(
        self, tmp_path
    ):
        """Backend-only projects (no frontend dir) keep frontend_kind=None."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "directory_map": {"backend": "server"},
        }))

        config = load_project_config(project_dir)
        assert config.frontend_kind is None

    def test_explicit_frontend_kind_is_honored(self, tmp_path):
        """An explicit frontend_kind in the JSON must not be overridden by migration."""
        project_dir = tmp_path / "my-app"
        config_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        config_dir.mkdir(parents=True)
        (config_dir / CONFIG_FILE).write_text(json.dumps({
            "app_name": "my-app",
            "directory_map": {"frontend": "client"},
            "frontend_kind": "cli",
        }))

        config = load_project_config(project_dir)
        assert config.frontend_kind == FrontendKind.CLI

    def test_save_reload_roundtrip_preserves_new_fields(self, tmp_path):
        project_dir = tmp_path / "my-app"
        (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True)

        original = ProjectConfig(
            app_name="my-app",
            frontend_kind=FrontendKind.MOBILE,
            uat_mode="spec_only",
        )
        save_project_config(project_dir, original)
        restored = load_project_config(project_dir)
        assert restored.frontend_kind == FrontendKind.MOBILE
        assert restored.uat_mode == "spec_only"


class TestProjectRegistry:
    """Tests for the global project registry."""

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

    def test_register_multiple_projects(self, tmp_path, monkeypatch):
        registry_file = tmp_path / "registry.json"
        monkeypatch.setattr("agentic_dev.config.REGISTRY_FILE", registry_file)
        monkeypatch.setattr("agentic_dev.config.GLOBAL_REGISTRY_DIR", tmp_path)

        register_project("app-a", tmp_path / "app-a")
        register_project("app-b", tmp_path / "app-b")
        registry = load_registry()
        assert len(registry) == 2

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
