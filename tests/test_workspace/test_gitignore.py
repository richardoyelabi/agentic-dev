"""Tests for ``ensure_managed_gitignore`` — the managed-block gitignore helper."""

from pathlib import Path

from agentic_dev.workspace.gitignore import (
    BLOCK_END,
    BLOCK_START,
    ensure_managed_gitignore,
)


def _init_git(path: Path) -> None:
    """Mark ``path`` as a git working tree (minimum: a ``.git`` directory)."""
    (path / ".git").mkdir()


class TestEnsureManagedGitignore:
    """``ensure_managed_gitignore`` writes a tagged block into ``.gitignore``.

    The block is idempotent, respects user-authored lines, and treats deletion
    of the closing marker as opt-out so it is never re-added.
    """

    def test_no_op_when_not_a_git_repo(self, tmp_path: Path) -> None:
        result = ensure_managed_gitignore(tmp_path, [".agentic-dev/"])

        assert result is False
        assert not (tmp_path / ".gitignore").exists()

    def test_creates_gitignore_when_missing(self, tmp_path: Path) -> None:
        _init_git(tmp_path)

        result = ensure_managed_gitignore(tmp_path, [".agentic-dev/", ".omc/"])

        assert result is True
        contents = (tmp_path / ".gitignore").read_text()
        assert BLOCK_START in contents
        assert BLOCK_END in contents
        assert ".agentic-dev/" in contents
        assert ".omc/" in contents

    def test_appends_block_preserves_user_lines(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n*.pyc\n")

        ensure_managed_gitignore(tmp_path, [".agentic-dev/"])

        contents = gitignore.read_text()
        assert "node_modules/\n" in contents
        assert "*.pyc\n" in contents
        assert BLOCK_START in contents
        assert ".agentic-dev/" in contents

    def test_idempotent_when_block_already_correct(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        entries = [".agentic-dev/", ".omc/"]
        ensure_managed_gitignore(tmp_path, entries)
        first = (tmp_path / ".gitignore").read_text()

        result = ensure_managed_gitignore(tmp_path, entries)

        assert result is False
        assert (tmp_path / ".gitignore").read_text() == first

    def test_updates_block_when_entries_change(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        ensure_managed_gitignore(tmp_path, [".agentic-dev/"])

        result = ensure_managed_gitignore(
            tmp_path, [".agentic-dev/", ".omc/", "scratch/"]
        )

        assert result is True
        contents = (tmp_path / ".gitignore").read_text()
        assert ".omc/" in contents
        assert "scratch/" in contents
        # block still appears exactly once
        assert contents.count(BLOCK_START) == 1
        assert contents.count(BLOCK_END) == 1

    def test_skips_entry_already_present_outside_block(
        self, tmp_path: Path
    ) -> None:
        _init_git(tmp_path)
        (tmp_path / ".gitignore").write_text(".omc/\nnode_modules/\n")

        ensure_managed_gitignore(tmp_path, [".agentic-dev/", ".omc/"])

        contents = (tmp_path / ".gitignore").read_text()
        # `.omc/` appears only once (in the user's section, not duplicated)
        assert contents.count(".omc/") == 1
        # the managed block contains `.agentic-dev/`
        assert ".agentic-dev/" in contents
        assert BLOCK_START in contents

    def test_user_deletion_of_closing_marker_means_opt_out(
        self, tmp_path: Path
    ) -> None:
        _init_git(tmp_path)
        gitignore = tmp_path / ".gitignore"
        ensure_managed_gitignore(tmp_path, [".agentic-dev/"])
        # Simulate the user deleting the closing marker as their opt-out signal,
        # but keeping the opening marker so the heuristic can detect it.
        contents = gitignore.read_text()
        contents = contents.replace(BLOCK_END + "\n", "")
        gitignore.write_text(contents)
        snapshot = gitignore.read_text()

        result = ensure_managed_gitignore(
            tmp_path, [".agentic-dev/", "scratch/"]
        )

        assert result is False
        assert gitignore.read_text() == snapshot
