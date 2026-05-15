"""Project discovery: infer or load the tracks for a project from its layout.

Discovery has two paths:

- ``load_track_override`` reads a committed ``agentic-dev.yaml`` at the project
  root. When present it is authoritative and the Claude agent is skipped.
- ``discover_tracks`` runs a Claude agent that walks the project tree and
  emits a JSON description of the tracks it found.

Together they let ``agentic-dev work`` infer structure without forcing the
user to declare tracks on the command line.
"""

from agentic_dev.discovery.agent import (
    DISCOVERY_PROMPT,
    DiscoveryResult,
    discover_tracks,
    parse_discovery_response,
)
from agentic_dev.discovery.override import OVERRIDE_FILENAME, load_track_override

__all__ = [
    "DISCOVERY_PROMPT",
    "DiscoveryResult",
    "OVERRIDE_FILENAME",
    "discover_tracks",
    "load_track_override",
    "parse_discovery_response",
]
