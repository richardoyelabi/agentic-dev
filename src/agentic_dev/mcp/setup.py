"""MCP prerequisite validation and guided setup helpers."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from agentic_dev.mcp.catalog import SERVICE_CATALOG, validate_service


def check_mcp_prerequisites(services: list[str], console: Console) -> bool:
    """Validate MCP services and display a status table.

    Returns True if all requested services are fully configured.
    Prints a Rich status table and setup instructions for any failing services.
    """
    if not services:
        return True

    results = [validate_service(s) for s in services]

    table = Table(title="MCP Service Status")
    table.add_column("Service", style="bold")
    table.add_column("Config", justify="center")
    table.add_column("Env Vars", justify="center")
    table.add_column("Status", justify="center")

    all_ready = True
    for result in results:
        config_icon = "[green]OK[/green]" if result.config_exists else "[red]Missing[/red]"
        if result.missing_env_vars:
            env_icon = f"[red]Missing: {', '.join(result.missing_env_vars)}[/red]"
        elif result.config_exists:
            env_icon = "[green]OK[/green]"
        else:
            env_icon = "[dim]N/A[/dim]"
        status_icon = "[green]Ready[/green]" if result.is_ready else "[red]Not Ready[/red]"

        table.add_row(result.service_name, config_icon, env_icon, status_icon)

        if not result.is_ready:
            all_ready = False

    console.print(table)

    if not all_ready:
        console.print()
        for result in results:
            if result.is_ready:
                continue
            info = SERVICE_CATALOG.get(result.service_name)
            if info and info.setup_instructions:
                console.print(
                    f"[bold yellow]Setup instructions for {info.name}:[/bold yellow]"
                )
                console.print(info.setup_instructions)
                console.print()

    return all_ready


def guide_figma_setup(console: Console) -> None:
    """Print Figma-specific setup guidance."""
    info = SERVICE_CATALOG["figma"]
    console.print("[bold yellow]Figma MCP Setup Required[/bold yellow]")
    console.print()
    console.print(
        "To import designs from Figma, you need a personal access token."
    )
    console.print()
    console.print(info.setup_instructions)
    console.print()
    console.print(
        "[dim]For more details, visit: "
        "https://www.figma.com/developers/api#access-tokens[/dim]"
    )
