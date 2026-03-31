"""Fixtures and configuration for end-to-end tests.

These tests make real Claude API calls and are skipped by default.
Run with: E2E=1 pytest tests/e2e/ -v -s --timeout=3600
"""

import os
import shutil
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip e2e tests unless E2E=1 environment variable is set."""
    if os.environ.get("E2E", "").strip() not in ("1", "true", "yes"):
        skip_marker = pytest.mark.skip(
            reason="E2E tests skipped. Set E2E=1 to run."
        )
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_marker)


APP_NAME = "e2e-counter-app"
PROJECTS_DIR = Path.home() / "projects"


@pytest.fixture(scope="session")
def projects_dir() -> Path:
    """Return the base projects directory, creating it if needed."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    return PROJECTS_DIR


@pytest.fixture(scope="session")
def app_name() -> str:
    """Return the test application name."""
    return APP_NAME


@pytest.fixture(scope="session")
def project_dir(projects_dir: Path, app_name: str) -> Path:
    """Return the full path to the test project directory."""
    return projects_dir / app_name


@pytest.fixture(scope="session", autouse=True)
def cleanup_project(project_dir: Path):
    """Optionally clean up the project directory after all tests.

    Set E2E_CLEANUP=1 to enable cleanup after tests complete.
    """
    yield project_dir
    if os.environ.get("E2E_CLEANUP", "").strip() in ("1", "true", "yes"):
        if project_dir.exists():
            shutil.rmtree(project_dir)
