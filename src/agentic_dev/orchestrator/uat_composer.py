"""Compose remediation input from a UAT report."""

from __future__ import annotations

from agentic_dev.tracks import Track


def compose_remediation_input(
    uat_report: str,
    project_name: str,
    tracks: list[Track] | None = None,
) -> str:
    """Wrap a UAT report as a change request for a remediation pipeline run.

    The returned string is suitable for writing as user_input to the document
    store before starting a remediation pipeline cycle. The phrasing lists
    the existing tracks so the planner knows the modify-in-place context.
    """
    if tracks:
        names = ", ".join(t.name for t in tracks)
        scope_phrase = f"existing tracks ({names})"
    else:
        scope_phrase = "existing"
    return (
        f"# Remediation Request for {project_name}\n"
        "\n"
        "Fix all failing acceptance criteria identified in the UAT report below.\n"
        f"The {scope_phrase} codebase already exists — modify existing code,\n"
        "do not start from scratch. Preserve all currently passing functionality.\n"
        "\n"
        "## UAT Report\n"
        "\n"
        f"{uat_report}\n"
    )
