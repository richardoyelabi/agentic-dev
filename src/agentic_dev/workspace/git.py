"""Git operations for workspace management using async subprocess calls."""

import asyncio
import subprocess
from pathlib import Path


async def _run_git(path: Path, *args: str) -> str:
    """Run a git command in the given directory and return stdout."""
    process = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {process.returncode}): "
            f"{stderr.decode().strip()}"
        )

    return stdout.decode().strip()


async def init_repo(path: Path) -> None:
    """Initialize a new git repository at the given path."""
    await _run_git(path, "init")


def init_repo_sync(path: Path) -> None:
    """Initialize a new git repository at the given path (synchronous).

    No-op if a .git directory already exists.
    """
    if (path / ".git").is_dir():
        return
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


async def create_branch(path: Path, branch_name: str) -> None:
    """Create and checkout a new branch."""
    await _run_git(path, "checkout", "-b", branch_name)


async def commit(path: Path, message: str, add_all: bool = True) -> None:
    """Create a git commit, optionally staging all changes first."""
    if add_all:
        await _run_git(path, "add", "-A")
    await _run_git(path, "commit", "-m", message)


async def has_changes(path: Path) -> bool:
    """Return True if there are staged, unstaged, or untracked changes."""
    output = await _run_git(path, "status", "--porcelain")
    return len(output) > 0


async def get_current_branch(path: Path) -> str:
    """Return the name of the current branch."""
    return await _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
