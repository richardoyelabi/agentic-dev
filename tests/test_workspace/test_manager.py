"""Tests for ``ensure_scaffold`` — the workspace-scaffolding free function."""

from pathlib import Path

import pytest

from agentic_dev.exceptions import WorkspaceError
from agentic_dev.workspace.gitignore import BLOCK_START
from agentic_dev.workspace.manager import ensure_scaffold


class TestEnsureScaffold:
    """``ensure_scaffold`` writes ``.agentic-dev/`` into an arbitrary directory.

    It is idempotent by default and only refuses to run when the caller
    explicitly demands a fresh project (``fresh=True``) and the metadata
    directory already exists.
    """

    def test_creates_metadata_in_empty_dir(self, tmp_path: Path) -> None:
        project_root = tmp_path / "empty-project"
        project_root.mkdir()

        result = ensure_scaffold(project_root)

        assert result == project_root
        assert (project_root / ".agentic-dev").is_dir()
        assert (project_root / ".agentic-dev" / "history").is_dir()
        assert (project_root / ".agentic-dev" / "logs").is_dir()
        assert (project_root / ".agentic-dev" / "sessions").is_dir()
        assert (project_root / ".agentic-dev" / "artifacts").is_dir()
        assert (project_root / ".agentic-dev" / "artifacts" / "qa").is_dir()
        assert (project_root / ".agentic-dev" / "artifacts" / ".git").is_dir()

    def test_creates_project_root_if_missing(self, tmp_path: Path) -> None:
        project_root = tmp_path / "does-not-exist-yet"

        ensure_scaffold(project_root)

        assert project_root.is_dir()
        assert (project_root / ".agentic-dev").is_dir()

    def test_does_not_touch_existing_files(self, tmp_path: Path) -> None:
        project_root = tmp_path / "real-project"
        (project_root / "backend").mkdir(parents=True)
        (project_root / "backend" / "main.py").write_text("print('hi')\n")
        (project_root / "frontend").mkdir()
        (project_root / "frontend" / "package.json").write_text('{"name": "x"}\n')
        (project_root / "README.md").write_text("# real-project\n")

        ensure_scaffold(project_root)

        assert (project_root / "backend" / "main.py").read_text() == "print('hi')\n"
        assert (project_root / "frontend" / "package.json").read_text() == '{"name": "x"}\n'
        assert (project_root / "README.md").read_text() == "# real-project\n"
        assert (project_root / ".agentic-dev").is_dir()

    def test_idempotent_when_metadata_already_exists(self, tmp_path: Path) -> None:
        project_root = tmp_path / "twice"
        ensure_scaffold(project_root)
        marker = project_root / ".agentic-dev" / "marker.txt"
        marker.write_text("preserve me")

        ensure_scaffold(project_root)

        assert marker.read_text() == "preserve me"

    def test_rejects_when_fresh_and_metadata_exists(self, tmp_path: Path) -> None:
        project_root = tmp_path / "already-scaffolded"
        ensure_scaffold(project_root)

        with pytest.raises(WorkspaceError, match="already"):
            ensure_scaffold(project_root, fresh=True)

    def test_fresh_allowed_when_metadata_absent(self, tmp_path: Path) -> None:
        project_root = tmp_path / "brand-new"
        ensure_scaffold(project_root, fresh=True)
        assert (project_root / ".agentic-dev").is_dir()

    def test_writes_managed_gitignore_block_in_git_repo(
        self, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "git-project"
        project_root.mkdir()
        (project_root / ".git").mkdir()

        ensure_scaffold(project_root)

        gitignore = project_root / ".gitignore"
        assert gitignore.exists()
        contents = gitignore.read_text()
        assert BLOCK_START in contents
        assert ".agentic-dev/" in contents
        assert ".omc/" in contents
        assert ".agentic-dev/secrets.env" in contents

    def test_no_gitignore_when_not_git_repo(self, tmp_path: Path) -> None:
        project_root = tmp_path / "non-git"
        project_root.mkdir()

        ensure_scaffold(project_root)

        assert not (project_root / ".gitignore").exists()
