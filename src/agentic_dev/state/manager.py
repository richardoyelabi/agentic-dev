"""State manager for atomic persistence of pipeline state."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    HISTORY_DIR,
    STATE_FILE,
)
from agentic_dev.exceptions import StateError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import StateSaveEvent, StateLoadEvent
from agentic_dev.state.models import PipelineState

_event_log = get_event_logger("state")


class StateManager:
    """Manages loading, saving, and archiving of pipeline state."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.metadata_dir = project_dir / AGENTIC_DEV_METADATA_DIR
        self.state_file = self.metadata_dir / STATE_FILE
        self.history_dir = self.metadata_dir / HISTORY_DIR

    def load(self) -> PipelineState:
        """Load pipeline state from disk.

        Raises StateError if the state file does not exist.
        """
        if not self.state_file.exists():
            raise StateError(
                f"State file not found: {self.state_file}. "
                "Have you initialized a project?"
            )
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        state = PipelineState.model_validate(data)
        emit(_event_log, StateLoadEvent(
            phase=str(state.phase),
            total_cost_usd=state.total_cost_usd,
            message=f"State loaded (phase={state.phase}, cost=${state.total_cost_usd:.4f})",
        ))
        return state

    def save(self, state: PipelineState) -> None:
        """Atomically save pipeline state.

        Writes to a temporary file then renames to avoid corruption on crash.
        Archives the previous state file to history/ with an ISO timestamp.
        """
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        # Archive existing state before overwriting
        if self.state_file.exists():
            self._archive_current_state()

        tmp_path = self.state_file.with_suffix(".json.tmp")
        state.updated_at = datetime.now(timezone.utc)
        tmp_path.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        shutil.move(str(tmp_path), str(self.state_file))
        emit(_event_log, StateSaveEvent(
            phase=str(state.phase),
            total_cost_usd=state.total_cost_usd,
            sprint_count=len(state.sprints),
            message=f"State saved (phase={state.phase}, cost=${state.total_cost_usd:.4f})",
        ))

    def exists(self) -> bool:
        """Check whether a state file already exists."""
        return self.state_file.exists()

    def create_initial(
        self, project_name: str, mode: Literal["new", "update", "remediate"] = "new"
    ) -> PipelineState:
        """Create and persist a fresh initial pipeline state."""
        state = PipelineState(project_name=project_name, mode=mode)
        self.save(state)
        return state

    def _archive_current_state(self) -> None:
        """Copy the current state file into history/ with a timestamped name."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive_path = self.history_dir / f"state-{timestamp}.json"
        shutil.copy2(str(self.state_file), str(archive_path))
