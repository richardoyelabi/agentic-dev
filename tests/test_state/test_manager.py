"""Tests for the StateManager class."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, HISTORY_DIR, STATE_FILE, STATE_LOCK_FILE
from agentic_dev.exceptions import StateError
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelinePhase


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return a temporary project directory."""
    return tmp_path / "my-project"


@pytest.fixture
def manager(project_dir: Path) -> StateManager:
    """Return a StateManager pointed at a temporary project."""
    return StateManager(project_dir)


class TestCreateInitial:
    def test_creates_state_file(self, manager: StateManager, project_dir: Path) -> None:
        state = manager.create_initial("my-project")

        state_path = project_dir / AGENTIC_DEV_METADATA_DIR / STATE_FILE
        assert state_path.exists()
        assert state.project_name == "my-project"
        assert state.phase == PipelinePhase.IDLE
        assert state.mode == "new"

    def test_create_initial_with_update_mode(self, manager: StateManager) -> None:
        state = manager.create_initial("my-project", mode="update")
        assert state.mode == "update"


class TestSaveAndLoad:
    def test_roundtrip(self, manager: StateManager) -> None:
        original = manager.create_initial("roundtrip-test")
        loaded = manager.load()

        assert loaded.project_name == original.project_name
        assert loaded.phase == original.phase
        assert loaded.mode == original.mode

    def test_atomic_write_no_tmp_lingering(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        manager.create_initial("tmp-check")
        tmp_path = project_dir / AGENTIC_DEV_METADATA_DIR / "state.json.tmp"
        assert not tmp_path.exists()

    def test_load_missing_file_raises_state_error(
        self, manager: StateManager
    ) -> None:
        with pytest.raises(StateError):
            manager.load()


class TestHistoryArchiving:
    def test_previous_state_archived_on_save(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        manager.create_initial("archive-test")
        history_dir = project_dir / AGENTIC_DEV_METADATA_DIR / HISTORY_DIR

        # First save creates the file; second save should archive the first
        state = manager.load()
        state.phase = PipelinePhase.INPUT_PROCESSING
        manager.save(state)

        archived_files = list(history_dir.glob("state-*.json"))
        assert len(archived_files) == 1

    def test_multiple_saves_accumulate_history(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        manager.create_initial("multi-archive")
        history_dir = project_dir / AGENTIC_DEV_METADATA_DIR / HISTORY_DIR

        for _ in range(3):
            state = manager.load()
            manager.save(state)

        archived_files = list(history_dir.glob("state-*.json"))
        assert len(archived_files) == 3


class TestExists:
    def test_exists_false_before_create(self, manager: StateManager) -> None:
        assert not manager.exists()

    def test_exists_true_after_create(self, manager: StateManager) -> None:
        manager.create_initial("exist-check")
        assert manager.exists()


def _save_with_delay(project_dir_str: str, phase_value: str, ready_fd: int) -> None:
    """Helper: load state, signal ready, save with a modified phase after delay."""
    project_dir = Path(project_dir_str)
    mgr = StateManager(project_dir)
    state = mgr.load()
    state.phase = PipelinePhase(phase_value)
    os.write(ready_fd, b"r")
    time.sleep(0.1)
    mgr.save(state)


class TestStateLocking:
    def test_save_creates_lock_file(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        manager.create_initial("lock-test")
        lock_path = project_dir / AGENTIC_DEV_METADATA_DIR / STATE_LOCK_FILE
        assert lock_path.exists()

    def test_load_creates_lock_file(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        manager.create_initial("lock-test")
        lock_path = project_dir / AGENTIC_DEV_METADATA_DIR / STATE_LOCK_FILE
        # Lock file created by save above; remove it to test load creates it
        lock_path.unlink()
        manager.load()
        assert lock_path.exists()

    def test_concurrent_saves_serialized(
        self, manager: StateManager, project_dir: Path
    ) -> None:
        """Two concurrent saves should not corrupt state."""
        manager.create_initial("concurrent-test")

        ready_r1, ready_w1 = os.pipe()
        ready_r2, ready_w2 = os.pipe()

        p1 = multiprocessing.Process(
            target=_save_with_delay,
            args=(str(project_dir), "INPUT_PROCESSING", ready_w1),
        )
        p2 = multiprocessing.Process(
            target=_save_with_delay,
            args=(str(project_dir), "SPRINT_PLANNING", ready_w2),
        )

        p1.start()
        p2.start()
        os.close(ready_w1)
        os.close(ready_w2)

        # Wait for both to be ready
        os.read(ready_r1, 1)
        os.read(ready_r2, 1)
        os.close(ready_r1)
        os.close(ready_r2)

        p1.join(timeout=5)
        p2.join(timeout=5)

        assert p1.exitcode == 0
        assert p2.exitcode == 0

        # State should be valid JSON and a valid PipelineState
        state = manager.load()
        assert state.phase in (PipelinePhase.INPUT_PROCESSING, PipelinePhase.SPRINT_PLANNING)
