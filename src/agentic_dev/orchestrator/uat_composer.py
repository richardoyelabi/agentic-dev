"""Compose remediation input from a UAT report."""

from __future__ import annotations

from agentic_dev.state.models import FrontendKind


_KIND_PHRASE: dict[FrontendKind | None, str] = {
    FrontendKind.WEB: "backend and frontend",
    FrontendKind.CLI: "backend and CLI",
    FrontendKind.DESKTOP: "backend and desktop app",
    FrontendKind.MOBILE: "backend and mobile app",
    FrontendKind.NONE: "backend",
    None: "existing",
}


def compose_remediation_input(
    uat_report: str,
    project_name: str,
    frontend_kind: FrontendKind | None,
) -> str:
    """Wrap a UAT report as a change request for a remediation pipeline run.

    The returned string is suitable for writing as user_input to the document
    store before starting a remediation pipeline cycle. The phrasing adapts to
    the project's FrontendKind so CLI / mobile / desktop projects don't get
    web-specific language.
    """
    kind_phrase = _KIND_PHRASE.get(frontend_kind, "existing")
    return (
        f"# Remediation Request for {project_name}\n"
        "\n"
        "Fix all failing acceptance criteria identified in the UAT report below.\n"
        f"The {kind_phrase} codebase already exists \u2014 modify existing code,\n"
        "do not start from scratch. Preserve all currently passing functionality.\n"
        "\n"
        "## UAT Report\n"
        "\n"
        f"{uat_report}\n"
    )
