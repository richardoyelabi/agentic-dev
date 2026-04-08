"""MCP prerequisite validation and guided setup helpers.

Validates that required MCP servers are configured in Claude Code's
native settings rather than maintaining a separate catalog.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from agentic_dev.mcp.claude_settings import (
    discover_mcp_servers,
    find_server_for_service,
)


def check_mcp_prerequisites(
    services: list[str],
    console: Console,
    project_dir: Path | None = None,
) -> bool:
    """Validate that MCP services are configured in Claude Code settings.

    Returns True if all requested services have a matching MCP server
    configured in the user's Claude Code environment.
    """
    if not services:
        return True

    env = discover_mcp_servers(project_dir=project_dir)

    table = Table(title="MCP Service Status")
    table.add_column("Service", style="bold")
    table.add_column("Configured", justify="center")
    table.add_column("Source", justify="center")
    table.add_column("Status", justify="center")

    all_ready = True
    missing_services: list[str] = []

    for service in services:
        entry = find_server_for_service(env, service)
        if entry is not None:
            table.add_row(
                service,
                "[green]Yes[/green]",
                f"[dim]{entry.source}[/dim]",
                "[green]Ready[/green]",
            )
        else:
            all_ready = False
            missing_services.append(service)
            table.add_row(
                service,
                "[red]No[/red]",
                "[dim]—[/dim]",
                "[red]Not Ready[/red]",
            )

    console.print(table)

    if missing_services:
        console.print()
        for service in missing_services:
            console.print(
                f"[bold yellow]To configure {service}:[/bold yellow]"
            )
            console.print(
                f"  Run [bold]claude mcp add {service}[/bold] or configure "
                f"it in Claude Code's settings UI."
            )
            console.print(
                "  See https://docs.anthropic.com/en/docs/claude-code/mcp "
                "for details."
            )
            console.print()

    return all_ready


def guide_figma_setup(console: Console) -> None:
    """Print Figma-specific setup guidance using Claude Code's MCP system."""
    console.print("[bold yellow]Figma MCP Setup Required[/bold yellow]")
    console.print()
    console.print(
        "To import designs from Figma, configure the Figma MCP server "
        "in Claude Code."
    )
    console.print()
    console.print("Option 1: Run [bold]claude mcp add figma[/bold]")
    console.print(
        "Option 2: Use Claude Code's authentication UI to connect Figma "
        "(supports OAuth)"
    )
    console.print()
    console.print(
        "[dim]For more details, visit: "
        "https://docs.anthropic.com/en/docs/claude-code/mcp[/dim]"
    )
