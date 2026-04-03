"""Tests for the WorkspaceManager."""

from pathlib import Path

import pytest

from agentic_dev.exceptions import WorkspaceError
from agentic_dev.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(base_dir=tmp_path)


class TestCreateProject:
    def test_creates_base_directories_only(self, workspace: WorkspaceManager) -> None:
        project_root = workspace.create_project("my-app")

        assert project_root.is_dir()
        assert (project_root / ".agentic-dev").is_dir()
        assert (project_root / ".agentic-dev" / "history").is_dir()
        assert (project_root / ".agentic-dev" / "logs").is_dir()
        assert (project_root / ".agentic-dev" / "sessions").is_dir()
        assert (project_root / "docs").is_dir()
        assert (project_root / "docs" / "qa_reports").is_dir()
        assert not (project_root / "frontend").exists()
        assert not (project_root / "backend").exists()

    def test_returns_project_root_path(self, workspace: WorkspaceManager) -> None:
        project_root = workspace.create_project("my-app")

        assert project_root == workspace.base_dir / "my-app"

    def test_raises_if_directory_already_exists(
        self, workspace: WorkspaceManager
    ) -> None:
        workspace.create_project("my-app")

        with pytest.raises(WorkspaceError, match="already exists"):
            workspace.create_project("my-app")


class TestCreateCodeDirs:
    def test_fullstack_creates_both_dirs(self, workspace: WorkspaceManager) -> None:
        project_root = workspace.create_project("my-app")
        workspace.create_code_dirs("my-app", "fullstack")

        assert (project_root / "frontend").is_dir()
        assert (project_root / "backend").is_dir()

    def test_frontend_only_creates_frontend_dir(self, workspace: WorkspaceManager) -> None:
        project_root = workspace.create_project("my-app")
        workspace.create_code_dirs("my-app", "frontend_only")

        assert (project_root / "frontend").is_dir()
        assert not (project_root / "backend").exists()

    def test_backend_only_creates_backend_dir(self, workspace: WorkspaceManager) -> None:
        project_root = workspace.create_project("my-app")
        workspace.create_code_dirs("my-app", "backend_only")

        assert not (project_root / "frontend").exists()
        assert (project_root / "backend").is_dir()

    def test_raises_for_missing_project(self, workspace: WorkspaceManager) -> None:
        with pytest.raises(WorkspaceError, match="does not exist"):
            workspace.create_code_dirs("nonexistent", "fullstack")


class TestGetProjectDir:
    def test_returns_path_for_existing_project(
        self, workspace: WorkspaceManager
    ) -> None:
        workspace.create_project("my-app")

        result = workspace.get_project_dir("my-app")

        assert result == workspace.base_dir / "my-app"

    def test_raises_for_missing_project(self, workspace: WorkspaceManager) -> None:
        with pytest.raises(WorkspaceError, match="does not exist"):
            workspace.get_project_dir("nonexistent")


class TestListProjects:
    def test_lists_projects_with_metadata_dir(
        self, workspace: WorkspaceManager
    ) -> None:
        workspace.create_project("alpha")
        workspace.create_project("beta")

        # Create a plain directory without .agentic-dev (should be excluded)
        (workspace.base_dir / "not-a-project").mkdir()

        result = workspace.list_projects()

        assert result == ["alpha", "beta"]

    def test_returns_empty_list_when_no_projects(
        self, workspace: WorkspaceManager
    ) -> None:
        assert workspace.list_projects() == []

    def test_returns_empty_list_when_base_dir_missing(
        self, tmp_path: Path
    ) -> None:
        manager = WorkspaceManager(base_dir=tmp_path / "nonexistent")

        assert manager.list_projects() == []
