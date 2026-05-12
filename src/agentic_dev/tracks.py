"""Track abstraction: the minimal flexible unit of work for agentic-dev.

A ``Track`` is one codebase within a project (e.g. ``web``, ``api``, ``worker``).
Projects default to a single track at the repo root; multi-codebase projects
declare each track explicitly via ``--track name::path::kind`` at ``new`` time.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


_SLUG_PATTERN = re.compile(r"^[a-z0-9_-]+$")


class TrackPhase(StrEnum):
    """Per-track sub-phase within a single sprint."""

    PENDING = "pending"
    DEV = "dev"
    QA = "qa"
    CORRECTION = "correction"
    COMPLETE = "complete"
    FAILED = "failed"


class Track(BaseModel):
    """One codebase within the project.

    ``kind`` and ``uat_kind`` are free-form strings (not closed enums) to allow
    the architect to invent new categories as projects demand. The dispatcher
    looks up the corresponding ``uat_<uat_kind>`` agent at runtime.
    """

    name: str
    path: str = "."
    kind: str = "generic"
    uat_kind: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                f"Track name must match [a-z0-9_-]+, got: {value!r}"
            )
        return value


class TrackProgress(BaseModel):
    """Runtime progress of a single track within a single sprint."""

    track_name: str
    phase: TrackPhase = TrackPhase.PENDING
    session_id: str | None = None
    failed_at_phase: TrackPhase | None = None


def parse_track_spec(spec: str) -> Track:
    """Parse a ``--track`` CLI flag value.

    Format: ``name`` or ``name::path`` or ``name::path::kind`` or
    ``name::path::kind::uat_kind``. The path defaults to the track name, and
    ``kind`` defaults to ``"generic"``.
    """
    parts = spec.split("::")
    if not parts or not parts[0]:
        raise ValueError(f"Track spec missing name: {spec!r}")
    name = parts[0]
    path = parts[1] if len(parts) > 1 and parts[1] else name
    kind = parts[2] if len(parts) > 2 and parts[2] else "generic"
    uat_kind = parts[3] if len(parts) > 3 and parts[3] else None
    return Track(name=name, path=path, kind=kind, uat_kind=uat_kind)


def expected_architecture_docs(tracks: list[Track]) -> list[str]:
    """Compute the architecture spec document names produced for these tracks.

    Each track produces ``<name>_spec``. If any track has ``kind == "api"`` the
    cross-track ``api_contract`` document is also produced.
    """
    docs = [f"{t.name}_spec" for t in tracks]
    if any(t.kind == "api" for t in tracks):
        docs.append("api_contract")
    return docs


def default_tracks() -> list[Track]:
    """Default single-track layout: one track named ``app`` at the repo root."""
    return [Track(name="app")]


class TrackList(BaseModel):
    """Container that round-trips a list of tracks via pydantic."""

    tracks: list[Track] = Field(default_factory=default_tracks)
