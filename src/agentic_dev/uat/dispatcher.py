"""Dispatches a UAT agent name based on project type + frontend kind.

Pure logic — no I/O, no engine or registry imports. See the design spec
section 5a for the full dispatch matrix.
"""

from __future__ import annotations

import re

from agentic_dev.state.models import FrontendKind, ProjectType


_KNOWN_DESKTOP_FRAMEWORKS = {"electron", "tauri"}

_DESKTOP_FRAMEWORK_HEADER_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?desktop_framework\s*[:\n]\s*([A-Za-z_][A-Za-z0-9_-]*)"
)


def pick_uat_agent(
    project_type: ProjectType,
    frontend_kind: FrontendKind,
    desktop_framework: str | None = None,
) -> str:
    """Return the UAT agent name for the given project configuration.

    Raises ValueError for any invalid combination (spec §5a).
    """
    if project_type == ProjectType.BACKEND_ONLY:
        if frontend_kind != FrontendKind.NONE:
            raise ValueError(
                f"Invalid combination: project_type={project_type.value} "
                f"with frontend_kind={frontend_kind.value} "
                "(backend-only projects require frontend_kind=none)"
            )
        return "uat_api"

    if frontend_kind == FrontendKind.NONE:
        raise ValueError(
            f"Invalid combination: project_type={project_type.value} "
            f"with frontend_kind={frontend_kind.value} "
            "(frontend_kind=none is only valid for backend-only projects)"
        )

    if frontend_kind == FrontendKind.WEB:
        return "uat_web"
    if frontend_kind == FrontendKind.CLI:
        return "uat_cli"
    if frontend_kind == FrontendKind.MOBILE:
        return "uat_mobile"
    if frontend_kind == FrontendKind.DESKTOP:
        if desktop_framework is None:
            raise ValueError(
                "desktop_framework is required when frontend_kind=desktop "
                "(expected 'electron' or 'tauri')"
            )
        normalized = desktop_framework.strip().lower()
        if normalized not in _KNOWN_DESKTOP_FRAMEWORKS:
            raise ValueError(
                f"Unknown desktop_framework {desktop_framework!r}; "
                f"expected one of {sorted(_KNOWN_DESKTOP_FRAMEWORKS)}"
            )
        return f"uat_desktop_{normalized}"

    raise ValueError(
        f"Unhandled frontend_kind={frontend_kind.value} "
        f"for project_type={project_type.value}"
    )


def _read_desktop_framework(frontend_spec_text: str) -> str | None:
    """Extract the ``desktop_framework`` header value from frontend_spec text.

    Accepts variants: ``desktop_framework: electron``, ``## desktop_framework: tauri``,
    or ``## desktop_framework\\nelectron``. Returns the normalized lowercase value
    if it matches a known framework, else None.
    """
    match = _DESKTOP_FRAMEWORK_HEADER_RE.search(frontend_spec_text)
    if not match:
        return None
    value = match.group(1).strip().lower()
    if value not in _KNOWN_DESKTOP_FRAMEWORKS:
        return None
    return value
