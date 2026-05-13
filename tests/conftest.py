"""Shared test fixtures for agentic-dev tests."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_registry(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect the global project registry into a tmp dir for every test.

    Without this, any test that calls ``register_project`` (directly or via
    ``agentic-dev new`` through the Typer test runner) writes to the user's
    real ``~/.agentic-dev/registry.json``, and later tests that resolve a
    project by name pick up stale entries from prior test runs.
    """
    fake_dir = tmp_path_factory.mktemp("_global_registry")
    monkeypatch.setattr("agentic_dev.config.GLOBAL_REGISTRY_DIR", fake_dir)
    monkeypatch.setattr(
        "agentic_dev.config.REGISTRY_FILE", fake_dir / "registry.json"
    )


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
