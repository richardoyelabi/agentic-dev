"""Dispatch a UAT agent name from a track's ``uat_kind``.

Pure logic — no I/O, no engine or registry imports. Lookup is direct:
``track.uat_kind`` -> ``uat_<uat_kind>``. The desktop case still picks
between ``uat_desktop_electron`` and ``uat_desktop_tauri`` based on a
framework hint that the architecture phase records in the track spec.
"""

from __future__ import annotations

import re

from agentic_dev.tracks import Track


_KNOWN_DESKTOP_FRAMEWORKS = {"electron", "tauri"}

_DESKTOP_FRAMEWORK_HEADER_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?desktop_framework\s*[:\n]\s*([A-Za-z_][A-Za-z0-9_-]*)"
)


def pick_uat_agent(track: Track, desktop_framework: str | None = None) -> str:
    """Return the UAT agent name to run for ``track``.

    Raises ``ValueError`` when the track has no ``uat_kind`` (caller should
    filter such tracks out) or when ``uat_kind == "desktop"`` without a known
    framework.
    """
    if not track.uat_kind:
        raise ValueError(
            f"Track {track.name!r} has no uat_kind; nothing to dispatch."
        )

    kind = track.uat_kind.strip().lower()
    if kind == "desktop":
        if desktop_framework is None:
            raise ValueError(
                "desktop_framework is required when uat_kind=desktop "
                "(expected 'electron' or 'tauri')"
            )
        normalized = desktop_framework.strip().lower()
        if normalized not in _KNOWN_DESKTOP_FRAMEWORKS:
            raise ValueError(
                f"Unknown desktop_framework {desktop_framework!r}; "
                f"expected one of {sorted(_KNOWN_DESKTOP_FRAMEWORKS)}"
            )
        return f"uat_desktop_{normalized}"
    return f"uat_{kind}"


def _read_desktop_framework(spec_text: str) -> str | None:
    """Extract the ``desktop_framework`` header value from a track spec.

    Accepts the same variants as the legacy implementation. Returns the
    normalized lowercase value when it matches a known framework, else None.
    """
    match = _DESKTOP_FRAMEWORK_HEADER_RE.search(spec_text)
    if not match:
        return None
    value = match.group(1).strip().lower()
    if value not in _KNOWN_DESKTOP_FRAMEWORKS:
        return None
    return value
