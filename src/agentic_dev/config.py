"""Global settings, default paths, and constants for the agentic-dev agency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.tracks import Track, default_tracks


DEFAULT_PROJECTS_DIR = Path.home() / "projects"

_PACKAGE_DIR = Path(__file__).parent  # always the agentic_dev/ package dir

AGENT_DEFINITIONS_DIR = _PACKAGE_DIR / "agents" / "definitions"
PROMPT_TEMPLATES_DIR = _PACKAGE_DIR / "prompts" / "templates"

AGENTIC_DEV_METADATA_DIR = ".agentic-dev"
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
HISTORY_DIR = "history"
LOGS_DIR = "logs"
AGENT_DUMPS_DIR = "agent_dumps"
RUNS_DIR = "runs"
LATEST_SYMLINK = "latest"
SESSIONS_DIR = "sessions"

STATE_LOCK_FILE = ".state.lock"
DOCS_LOCK_FILE = ".docs.lock"
SESSIONS_LOCK_FILE = ".sessions.lock"

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
}

# ---------------------------------------------------------------------------
# Rate-limit pause (pipeline-level) settings
# ---------------------------------------------------------------------------

# Maximum wait allowed during a single rate-limit pause before the pipeline
# bails out to FAILED.  Must comfortably cover Anthropic's 5-hour quota window.
RATE_LIMIT_PAUSE_MAX_SECONDS = 21_600  # 6 hours

# Granularity of the CLI's sleep loop; keeps shutdown (Ctrl+C) responsive.
RATE_LIMIT_PAUSE_POLL_INTERVAL_SECONDS = 10

# Safeguard against pathological pause loops — e.g. the usage API flapping or
# the runner repeatedly treating healthy failures as rate limits.  After this
# many back-to-back pauses the CLI aborts with a clear error.
MAX_CONSECUTIVE_RATE_LIMIT_PAUSES = 5

DOCUMENT_SEPARATOR = "<!-- DOCUMENT: {name} -->"
DOCUMENT_SEPARATOR_PATTERN = r"<!-- DOCUMENT: (\w+) -->"

GLOBAL_REGISTRY_DIR = Path.home() / ".agentic-dev"
REGISTRY_FILE = GLOBAL_REGISTRY_DIR / "registry.json"


# ---------------------------------------------------------------------------
# Project configuration models
# ---------------------------------------------------------------------------


class ExternalSource(BaseModel):
    """A tracked external source (codebase path or Figma URL) with annotation."""

    value: str
    annotation: str = ""


class ProjectConfig(BaseModel):
    """Full project configuration stored in .agentic-dev/config.json."""

    app_name: str
    tracks: list[Track] = Field(default_factory=default_tracks)
    sources: dict[str, list[ExternalSource]] = Field(default_factory=dict)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    uat_mode: Literal["spec_only", "full"] = "full"


# ---------------------------------------------------------------------------
# Config loading with migration from old CheckpointConfig format
# ---------------------------------------------------------------------------

_OLD_FORMAT_KEYS = {"after_design", "after_each_sprint", "before_uat"}


def load_project_config(project_dir: Path) -> ProjectConfig:
    """Load ProjectConfig from a project's config.json, migrating if needed.

    Old format (flat CheckpointConfig):
        {"after_design": true, "after_each_sprint": false, "before_uat": false}

    New format (ProjectConfig):
        {"app_name": "...", "checkpoint": {...}, "directory_map": {...}, ...}
    """
    config_path = (
        project_dir / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
    )
    if not config_path.exists():
        return ProjectConfig(app_name=project_dir.name)

    data = json.loads(config_path.read_text())

    if _OLD_FORMAT_KEYS & data.keys():
        checkpoint_data = {
            k: data.pop(k) for k in list(data.keys()) if k in _OLD_FORMAT_KEYS
        }
        data["checkpoint"] = checkpoint_data

    if "app_name" not in data:
        data["app_name"] = project_dir.name

    # Drop any legacy fields that no longer belong on ProjectConfig.
    for legacy in ("directory_map", "frontend_kind", "sync_ignores"):
        data.pop(legacy, None)

    return ProjectConfig.model_validate(data)


def save_project_config(project_dir: Path, config: ProjectConfig) -> None:
    """Save ProjectConfig to a project's config.json in the new format."""
    config_path = (
        project_dir / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config.model_dump_json(indent=2) + "\n"
    )


# ---------------------------------------------------------------------------
# Global project registry
# ---------------------------------------------------------------------------


def load_registry() -> dict[str, str]:
    """Load the global project registry mapping app names to absolute paths."""
    if not REGISTRY_FILE.exists():
        return {}
    return json.loads(REGISTRY_FILE.read_text())


def register_project(app_name: str, path: Path) -> None:
    """Register a project in the global registry."""
    registry = load_registry()
    registry[app_name] = str(path.resolve())
    GLOBAL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2) + "\n")


def resolve_project_path(app_name: str, base_dir: Path = DEFAULT_PROJECTS_DIR) -> Path:
    """Resolve a project path by checking the registry first, then base_dir.

    Returns the project directory path. Does not validate that it exists.
    """
    registry = load_registry()
    if app_name in registry:
        return Path(registry[app_name])
    return base_dir / app_name
