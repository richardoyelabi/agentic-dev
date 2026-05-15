"""Loader for the optional ``agentic-dev.yaml`` track-override file."""

from __future__ import annotations

from pathlib import Path

import yaml

from agentic_dev.exceptions import AgenticDevError
from agentic_dev.tracks import Track


OVERRIDE_FILENAME = "agentic-dev.yaml"


def load_track_override(project_root: Path) -> list[Track] | None:
    """Return the user-declared tracks from ``agentic-dev.yaml`` or ``None``.

    The override file lives at the project root and is committed to the
    user's repo. When present it trumps automatic discovery. Format::

        tracks:
          - name: backend
            path: backend
            kind: api
            uat_kind: api
          - name: frontend
            path: frontend
            kind: web
            uat_kind: web
    """
    override_path = project_root / OVERRIDE_FILENAME
    if not override_path.exists():
        return None

    data = yaml.safe_load(override_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AgenticDevError(
            f"{OVERRIDE_FILENAME} must be a mapping at the top level, "
            f"got: {type(data).__name__}"
        )

    raw_tracks = data.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise AgenticDevError(
            f"{OVERRIDE_FILENAME} must list at least one track under `tracks:`"
        )

    return [Track.model_validate(item) for item in raw_tracks]
