"""Compose remediation input from a UAT report."""


def compose_remediation_input(uat_report: str, project_name: str) -> str:
    """Wrap a UAT report as a change request for a remediation pipeline run.

    The returned string is suitable for writing as user_input to the document
    store before starting a remediation pipeline cycle.
    """
    return (
        f"# Remediation Request for {project_name}\n"
        "\n"
        "Fix all failing acceptance criteria identified in the UAT report below.\n"
        "The backend and frontend codebases already exist \u2014 modify existing code,\n"
        "do not start from scratch. Preserve all currently passing functionality.\n"
        "\n"
        "## UAT Report\n"
        "\n"
        f"{uat_report}\n"
    )
