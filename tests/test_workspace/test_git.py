"""Tests for async git operations."""

from pathlib import Path

import pytest

from agentic_dev.workspace.git import (
    commit,
    create_branch,
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
