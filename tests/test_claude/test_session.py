"""Tests for the SessionStore."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from agentic_dev.claude.session import SessionStore
from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, SESSIONS_LOCK_FILE


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return a temporary project directory."""
    return tmp_path / "my-project"


class TestSessionStoreBasic:
    def test_save_and_load_roundtrip(self, project_dir: Path) -> None:
        SessionStore.save_session("architect", 1, "sess-abc123", project_dir)
        result = SessionStore.load_session("architect", 1, project_dir)
        assert result == "sess-abc123"

    def test_load_missing_returns_none(self, project_dir: Path) -> None:
        result = SessionStore.load_session("nonexistent", None, project_dir)
        assert result is None

    def test_save_without_sprint(self, project_dir: Path) -> None:
        SessionStore.save_session("developer", None, "sess-xyz", project_dir)
        result = SessionStore.load_session("developer", None, project_dir)
        assert result == "sess-xyz"

    def test_different_sprints_independent(self, project_dir: Path) -> None:
        SessionStore.save_session("developer", 1, "sess-s1", project_dir)
        SessionStore.save_session("developer", 2, "sess-s2", project_dir)
        assert SessionStore.load_session("developer", 1, project_dir) == "sess-s1"
        assert SessionStore.load_session("developer", 2, project_dir) == "sess-s2"


def _save_session_with_delay(
    project_dir_str: str, agent_name: str, session_id: str, ready_fd: int
) -> None:
    """Helper: save a session after signaling readiness."""
    project_dir = Path(project_dir_str)
    os.write(ready_fd, b"r")
    time.sleep(0.05)
    SessionStore.save_session(agent_name, 1, session_id, project_dir)


class TestSessionLocking:
    def test_save_creates_lock_file(self, project_dir: Path) -> None:
        SessionStore.save_session("architect", 1, "sess-123", project_dir)
        lock_path = project_dir / AGENTIC_DEV_METADATA_DIR / SESSIONS_LOCK_FILE
        assert lock_path.exists()

    def test_load_creates_lock_file(self, project_dir: Path) -> None:
        SessionStore.save_session("architect", 1, "sess-123", project_dir)
        lock_path = project_dir / AGENTIC_DEV_METADATA_DIR / SESSIONS_LOCK_FILE
        lock_path.unlink()
        SessionStore.load_session("architect", 1, project_dir)
        assert lock_path.exists()

    def test_concurrent_saves_serialized(self, project_dir: Path) -> None:
        """Two concurrent saves should not corrupt session files."""
        # Create the metadata dir so both processes can find it
        (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir(parents=True, exist_ok=True)

        ready_r1, ready_w1 = os.pipe()
        ready_r2, ready_w2 = os.pipe()

        p1 = multiprocessing.Process(
            target=_save_session_with_delay,
            args=(str(project_dir), "architect", "sess-from-p1", ready_w1),
        )
        p2 = multiprocessing.Process(
            target=_save_session_with_delay,
            args=(str(project_dir), "architect", "sess-from-p2", ready_w2),
        )

        p1.start()
        p2.start()
        os.close(ready_w1)
        os.close(ready_w2)

        os.read(ready_r1, 1)
        os.read(ready_r2, 1)
        os.close(ready_r1)
        os.close(ready_r2)

        p1.join(timeout=5)
        p2.join(timeout=5)

        assert p1.exitcode == 0
        assert p2.exitcode == 0

        result = SessionStore.load_session("architect", 1, project_dir)
        assert result in ("sess-from-p1", "sess-from-p2")
