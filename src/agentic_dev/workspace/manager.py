"""Workspace manager for creating and managing agentic-dev project directories."""

from pathlib import Path

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    HISTORY_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    resolve_project_path,
)
from agentic_dev.exceptions import WorkspaceError
from agentic_dev.tracks import Track
from agentic_dev.workspace.git import init_repo_sync


class WorkspaceManager:
    """Creates and manages project directory structures."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def create_project(self, app_name: str) -> Path:
        """Create the project skeleton.

        The project root contains only ``.agentic-dev/`` (with artifacts/
        as the git-tracked artifact store) plus whatever track directories
        the user declares later. There is no top-level ``docs/`` directory.
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

        artifacts_dir = metadata_dir / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "qa").mkdir()
        init_repo_sync(artifacts_dir)

        return project_root

    def create_track_dirs(self, app_name: str, tracks: list[Track]) -> None:
        """Materialise each declared track's working directory.

        Raises WorkspaceError if the project directory does not exist.
        """
        project_root = self.get_project_dir(app_name)
        for track in tracks:
            (project_root / track.path).mkdir(parents=True, exist_ok=True)

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

    def list_projects(self) -> list[str]:
        """List project names (directories that contain .agentic-dev/)."""
        if not self.base_dir.exists():
            return []

        return sorted(
            entry.name
            for entry in self.base_dir.iterdir()
            if entry.is_dir() and (entry / AGENTIC_DEV_METADATA_DIR).is_dir()
        )
