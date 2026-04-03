"""Session ID persistence for Claude CLI agent runs."""

import json
from pathlib import Path

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, SESSIONS_DIR


class SessionStore:
    """Saves and loads Claude CLI session IDs to JSON files.

    Session files live under ``<project>/.agentic-dev/sessions/`` and are keyed
    by agent name plus an optional sprint number.
    """

    @staticmethod
    def _session_file(agent_name: str, sprint: int | None, project_dir: Path) -> Path:
        """Build the path to a session JSON file."""
        sessions_dir = project_dir / AGENTIC_DEV_METADATA_DIR / SESSIONS_DIR
        suffix = f"-sprint{sprint}" if sprint is not None else ""
        return sessions_dir / f"{agent_name}{suffix}.json"

    @staticmethod
    def save_session(
        agent_name: str,
        sprint: int | None,
        session_id: str,
        project_dir: Path,
    ) -> None:
        """Persist a session ID for later resumption.

        Creates the sessions directory if it does not exist.
        """
        path = SessionStore._session_file(agent_name, sprint, project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "agent_name": agent_name,
            "sprint": sprint,
            "session_id": session_id,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def load_session(
        agent_name: str,
        sprint: int | None,
        project_dir: Path,
    ) -> str | None:
        """Load a previously saved session ID.

        Returns:
            The session ID string, or None if no session file exists.
        """
        path = SessionStore._session_file(agent_name, sprint, project_dir)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        result: str | None = data.get("session_id")
        return result
