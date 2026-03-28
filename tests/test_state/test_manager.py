"""Tests for the StateManager class."""

from pathlib import Path

import pytest

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, HISTORY_DIR, STATE_FILE
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
