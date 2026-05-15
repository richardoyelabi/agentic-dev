"""Parse the ``.agentic-dev/secrets.env`` template.

The environment detector writes a ``.env``-style file with three kinds of
entries:

- Auto-fillable values (random crypto keys, ``localhost`` URLs): pre-filled
  by the detector.
- Mock-available values (``OPENAI_BASE_URL=http://localhost:8080``):
  pre-filled by the detector.
- Human-required secrets (OAuth client IDs, paid API keys): written as
  ``KEY=<FILL ME: hint>`` placeholders the user must replace before UAT can
  run.

``parse_secrets_template`` classifies the entries so the engine can decide
whether to pause for human input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_PLACEHOLDER_RE = re.compile(r"^<FILL ME(:.*)?>$")


@dataclass(frozen=True)
class SecretsState:
    """The parsed state of a ``secrets.env`` file."""

    filled: dict[str, str] = field(default_factory=dict)
    unfilled_required: list[str] = field(default_factory=list)

    def has_unfilled_required(self) -> bool:
        return bool(self.unfilled_required)


def parse_secrets_template(path: Path) -> SecretsState:
    """Parse a ``.env``-style template into a ``SecretsState``."""
    if not path.exists():
        return SecretsState()

    filled: dict[str, str] = {}
    unfilled: list[str] = []

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = _strip_quotes(value.strip())
        if _PLACEHOLDER_RE.match(value):
            unfilled.append(key)
        else:
            filled[key] = value

    return SecretsState(filled=filled, unfilled_required=unfilled)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
