"""Tests for async git operations."""

from pathlib import Path

import pytest

from agentic_dev.workspace.git import (
    _run_git,
    commit,
    create_branch,
    get_committed_content,
    get_current_branch,
    has_changes,
    init_repo,
    init_repo_sync,
)


@pytest.fixture
def git_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for git operations."""
    return tmp_path


async def test_init_repo_creates_git_directory(git_dir: Path) -> None:
    await init_repo(git_dir)

    assert (git_dir / ".git").is_dir()


async def test_commit_creates_a_commit(git_dir: Path) -> None:
    await init_repo(git_dir)

    # Configure git user for the test repo
    import asyncio

    await asyncio.create_subprocess_exec(
        "git", "config", "user.email", "test@example.com", cwd=git_dir
    )
    await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "Test User", cwd=git_dir
    )

    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    # Verify the commit exists in git log
    process = await asyncio.create_subprocess_exec(
        "git", "log", "--oneline",
        cwd=git_dir,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    log_output = stdout.decode().strip()

    assert "Initial commit" in log_output


async def test_create_branch_switches_to_new_branch(git_dir: Path) -> None:
    await init_repo(git_dir)

    # Need at least one commit before creating branches
    import asyncio

    await asyncio.create_subprocess_exec(
        "git", "config", "user.email", "test@example.com", cwd=git_dir
    )
    await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "Test User", cwd=git_dir
    )
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    await create_branch(git_dir, "feature/new-stuff")

    current = await get_current_branch(git_dir)
    assert current == "feature/new-stuff"


async def _init_with_config(git_dir: Path) -> None:
    """Initialize a git repo with user config for committing."""
    import asyncio

    await init_repo(git_dir)
    await asyncio.create_subprocess_exec(
        "git", "config", "user.email", "test@example.com", cwd=git_dir
    )
    await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "Test User", cwd=git_dir
    )


async def test_has_changes_returns_false_on_clean_repo(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    assert await has_changes(git_dir) is False


async def test_has_changes_returns_true_with_new_file(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    (git_dir / "new_file.txt").write_text("new content")

    assert await has_changes(git_dir) is True


async def test_has_changes_returns_true_with_modified_file(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    (git_dir / "README.md").write_text("# Updated")

    assert await has_changes(git_dir) is True


def test_init_repo_sync_creates_git_directory(git_dir: Path) -> None:
    init_repo_sync(git_dir)

    assert (git_dir / ".git").is_dir()


def test_init_repo_sync_is_idempotent(git_dir: Path) -> None:
    init_repo_sync(git_dir)
    init_repo_sync(git_dir)

    assert (git_dir / ".git").is_dir()


async def test_get_committed_content_returns_file_content(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "spec.md").write_text("# My Spec\nContent here\n")
    await commit(git_dir, "Add spec")

    result = await get_committed_content(git_dir, "spec.md")

    assert result == "# My Spec\nContent here"


async def test_get_committed_content_returns_none_for_missing_file(
    git_dir: Path,
) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    result = await get_committed_content(git_dir, "nonexistent.md")

    assert result is None


async def test_has_changes_ignores_dirty_submodules(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")

    # Create a nested git repo and commit it in the parent
    nested = git_dir / "sub"
    nested.mkdir()
    await init_repo(nested)
    await _init_with_config(nested)
    (nested / "file.txt").write_text("content")
    await commit(nested, "sub init")
    await commit(git_dir, "Initial commit with nested repo")

    # Dirty the nested repo with untracked content
    (nested / "untracked.txt").write_text("dirty content")

    assert await has_changes(git_dir) is False


async def test_run_git_error_includes_stdout_when_stderr_empty(git_dir: Path) -> None:
    await _init_with_config(git_dir)
    (git_dir / "README.md").write_text("# Hello")
    await commit(git_dir, "Initial commit")

    # git commit with nothing staged produces stdout message, empty stderr
    with pytest.raises(RuntimeError, match="nothing to commit"):
        await _run_git(git_dir, "commit", "-m", "empty")
