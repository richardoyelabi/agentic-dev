"""Workspace manager for creating and managing agentic-dev project directories."""

from pathlib import Path

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    DirectoryMap,
    DOCS_DIR,
    HISTORY_DIR,
    LOGS_DIR,
    QA_REPORTS_DIR,
    SESSIONS_DIR,
    register_project,
    resolve_project_path,
)
from agentic_dev.exceptions import WorkspaceError
from agentic_dev.workspace.git import init_repo_sync


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
        init_repo_sync(docs_dir)

        return project_root

    def create_code_dirs(
        self,
        app_name: str,
        project_type: str,
        directory_map: DirectoryMap | None = None,
    ) -> None:
        """Create code directories based on project type.

        Uses directory_map to resolve directory names if provided,
        falling back to "frontend"/"backend" defaults.

        Raises WorkspaceError if the project directory does not exist.
        """
        project_root = self.get_project_dir(app_name)
        frontend_name = (directory_map.frontend if directory_map else None) or "frontend"
        backend_name = (directory_map.backend if directory_map else None) or "backend"

        if project_type in ("fullstack", "frontend_only"):
            (project_root / frontend_name).mkdir(exist_ok=True)
        if project_type in ("fullstack", "backend_only"):
            (project_root / backend_name).mkdir(exist_ok=True)

    def get_project_dir(self, app_name: str) -> Path:
        """Return the project root path.

        Checks the global project registry first, then falls back
        to base_dir / app_name.

        Raises WorkspaceError if the project directory does not exist.
        """
        project_root = resolve_project_path(app_name, self.base_dir)

        if not project_root.exists():
            raise WorkspaceError(
                f"Project directory does not exist: {project_root}"
            )

        return project_root

    def adopt_project(self, project_path: Path, app_name: str) -> Path:
        """Initialize agentic-dev metadata in an existing project directory.

        Creates .agentic-dev/ and docs/ directories in-place. If the project
        already has a docs/ directory, creates docs/agentic-dev/ instead.
        Registers the project in the global registry.

        Returns the project root path.
        Raises WorkspaceError if the path does not exist or is already adopted.
        """
        if not project_path.exists():
            raise WorkspaceError(
                f"Project directory does not exist: {project_path}"
            )

        metadata_dir = project_path / AGENTIC_DEV_METADATA_DIR
        if metadata_dir.exists():
            raise WorkspaceError(
                f"Project already has {AGENTIC_DEV_METADATA_DIR}/: {project_path}"
            )

        metadata_dir.mkdir(parents=True)
        (metadata_dir / HISTORY_DIR).mkdir()
        (metadata_dir / LOGS_DIR).mkdir()
        (metadata_dir / SESSIONS_DIR).mkdir()

        existing_docs = project_path / DOCS_DIR
        if existing_docs.exists():
            docs_dir = existing_docs / "agentic-dev"
        else:
            docs_dir = existing_docs
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / QA_REPORTS_DIR).mkdir(exist_ok=True)
        init_repo_sync(docs_dir)

        register_project(app_name, project_path)

        return project_path

    def list_projects(self) -> list[str]:
        """List project names (directories that contain .agentic-dev/)."""
        if not self.base_dir.exists():
            return []

        return sorted(
            entry.name
            for entry in self.base_dir.iterdir()
            if entry.is_dir() and (entry / AGENTIC_DEV_METADATA_DIR).is_dir()
        )
