"""Shared test fixtures for agentic-dev tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory with the expected structure."""
    project_dir = tmp_path / "test-app"
    project_dir.mkdir()
    (project_dir / ".agentic-dev").mkdir()
    (project_dir / ".agentic-dev" / "history").mkdir()
    (project_dir / ".agentic-dev" / "logs").mkdir()
    (project_dir / ".agentic-dev" / "sessions").mkdir()
    (project_dir / "docs").mkdir()
    (project_dir / "docs" / "qa_reports").mkdir()
    (project_dir / "frontend").mkdir()
    (project_dir / "backend").mkdir()
    return project_dir
