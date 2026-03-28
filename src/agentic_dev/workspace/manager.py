"""Workspace manager for creating and managing agentic-dev project directories."""

from pathlib import Path

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    DOCS_DIR,
    HISTORY_DIR,
    LOGS_DIR,
    QA_REPORTS_DIR,
    SESSIONS_DIR,
)
from agentic_dev.exceptions import WorkspaceError


class WorkspaceManager:
    """Creates and manages project directory structures."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def create_project(self, app_name: str) -> Path:
        """Create the full project directory structure.

        Returns the project root path.
        Raises WorkspaceError if the directory already exists.
        """
        project_root = self.base_dir / app_name

        if project_root.exists():
            raise WorkspaceError(
                f"Project directory already exists: {project_root}"
            )

        metadata_dir = project_root / AGENTIC_DEV_METADATA_DIR
        metadata_dir.mkdir(parents=True)
        (metadata_dir / HISTORY_DIR).mkdir()
        (metadata_dir / LOGS_DIR).mkdir()
        (metadata_dir / SESSIONS_DIR).mkdir()

        docs_dir = project_root / DOCS_DIR
        docs_dir.mkdir()
        (docs_dir / QA_REPORTS_DIR).mkdir()

        (project_root / "frontend").mkdir()
        (project_root / "backend").mkdir()

        return project_root

    def get_project_dir(self, app_name: str) -> Path:
        """Return the project root path.

        Raises WorkspaceError if the project directory does not exist.
        """
        project_root = self.base_dir / app_name

        if not project_root.exists():
            raise WorkspaceError(
                f"Project directory does not exist: {project_root}"
            )

        return project_root

    def list_projects(self) -> list[str]:
        """List project names (directories that contain .agentic-dev/)."""
        if not self.base_dir.exists():
            return []

        return sorted(
            entry.name
            for entry in self.base_dir.iterdir()
            if entry.is_dir() and (entry / AGENTIC_DEV_METADATA_DIR).is_dir()
        )
