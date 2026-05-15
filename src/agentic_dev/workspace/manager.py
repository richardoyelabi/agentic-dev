"""Workspace scaffolding for agentic-dev projects.

A project is whatever directory contains ``.agentic-dev/`` — there is no
global registry and no app-name concept. ``ensure_scaffold`` is the single
function that writes the metadata tree; everything else (project resolution,
track directories) is the caller's responsibility.
"""

from pathlib import Path

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    HISTORY_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
)
from agentic_dev.exceptions import WorkspaceError
from agentic_dev.workspace.git import init_repo_sync


def ensure_scaffold(project_root: Path, fresh: bool = False) -> Path:
    """Scaffold ``.agentic-dev/`` metadata inside ``project_root``.

    Creates ``project_root`` if it doesn't exist, then writes the standard
    metadata tree (``history/``, ``logs/``, ``sessions/``, ``artifacts/qa/``)
    and initialises a git repo inside ``artifacts/``. Pre-existing files in
    the project root are left untouched.

    The operation is idempotent by default: if ``.agentic-dev/`` already
    exists, the function returns without modifying anything. When ``fresh``
    is True, an existing metadata directory causes a ``WorkspaceError``
    instead.
    """
    project_root.mkdir(parents=True, exist_ok=True)

    metadata_dir = project_root / AGENTIC_DEV_METADATA_DIR
    if metadata_dir.exists():
        if fresh:
            raise WorkspaceError(
                f"Project already initialised at {project_root}"
            )
        return project_root

    metadata_dir.mkdir()
    (metadata_dir / HISTORY_DIR).mkdir()
    (metadata_dir / LOGS_DIR).mkdir()
    (metadata_dir / SESSIONS_DIR).mkdir()

    artifacts_dir = metadata_dir / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "qa").mkdir()
    init_repo_sync(artifacts_dir)

    return project_root
