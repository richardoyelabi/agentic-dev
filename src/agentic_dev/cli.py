"""CLI entry point for the agentic-dev agency."""

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    CONFIG_FILE,
    DEFAULT_PROJECTS_DIR,
    LOGS_DIR,
)
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgenticDevError, CheckpointPause
from agentic_dev.orchestrator.checkpoint import CheckpointConfig, from_autonomy_level
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelinePhase, PipelineState
from agentic_dev.workspace.manager import WorkspaceManager

console = Console()

app = typer.Typer(
    name="agentic-dev",
    help="Autonomous software development agency powered by Claude Code CLI.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_workspace_manager(path: str | None) -> WorkspaceManager:
    """Create a WorkspaceManager rooted at the given or default path."""
    base_dir = Path(path) if path else DEFAULT_PROJECTS_DIR
    return WorkspaceManager(base_dir=base_dir)


def _display_checkpoint(state: PipelineState, project_dir: Path) -> None:
    """Display a Rich panel when the pipeline pauses at a checkpoint."""
    doc_store = DocumentStore(project_dir)
    docs = doc_store.list_documents()
    docs_text = "\n".join(f"  - {d}" for d in docs) if docs else "  (none)"

    panel_content = (
        f"[bold]Project:[/bold] {state.project_name}\n"
        f"[bold]Phase:[/bold] {state.phase}\n"
        f"[bold]Total cost:[/bold] ${state.total_cost_usd:.4f}\n\n"
        f"[bold]Documents produced:[/bold]\n{docs_text}\n\n"
        "Review the documents in the [cyan]docs/[/cyan] directory, then run:\n"
        f"  [green]agentic-dev resume {state.project_name}[/green]\n"
        "Optionally provide feedback with [green]--feedback[/green]."
    )
    console.print(Panel(panel_content, title="Pipeline Paused at Checkpoint", border_style="yellow"))


def _display_status(state: PipelineState) -> None:
    """Display a Rich table summarising the pipeline state."""
    table = Table(title=f"Project: {state.project_name}")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Phase", str(state.phase))
    table.add_row("Mode", state.mode)
    table.add_row("Created", state.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    table.add_row("Updated", state.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    table.add_row("Total Cost", f"${state.total_cost_usd:.4f}")

    if state.error:
        table.add_row("Error", f"[red]{state.error}[/red]")

    console.print(table)

    if state.sprints:
        sprint_table = Table(title="Sprints")
        sprint_table.add_column("#", justify="right")
        sprint_table.add_column("Name")
        sprint_table.add_column("Status")

        for sprint in state.sprints:
            status_style = "green" if sprint.status == "complete" else "yellow"
            if sprint.status == "failed":
                status_style = "red"
            sprint_table.add_row(
                str(sprint.sprint_number),
                sprint.name,
                f"[{status_style}]{sprint.status}[/{status_style}]",
            )

        console.print(sprint_table)


def _display_error(error: Exception) -> None:
    """Display an error using Rich console."""
    console.print(f"[bold red]Error:[/bold red] {error}")


def _load_config(project_dir: Path) -> CheckpointConfig:
    """Load CheckpointConfig from the project's config.json."""
    config_path = project_dir / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
    if not config_path.exists():
        return CheckpointConfig()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return CheckpointConfig.model_validate(data)


def _save_config(project_dir: Path, config: CheckpointConfig) -> None:
    """Save CheckpointConfig to the project's config.json."""
    config_path = project_dir / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")


def _collect_user_requirements() -> str:
    """Prompt the user to type or paste their project requirements."""
    if not sys.stdin.isatty():
        return sys.stdin.read()

    console.print(
        "[bold]Enter your project requirements.[/bold]\n"
        "Type or paste your description, then press Enter twice to finish.\n"
    )
    lines: list[str] = []
    empty_count = 0
    while True:
        line = Prompt.ask("", default="")
        if line == "":
            empty_count += 1
            if empty_count >= 2:
                break
            lines.append("")
        else:
            empty_count = 0
            lines.append(line)

    return "\n".join(lines).strip()


def _run_pipeline(project_dir: Path, state: PipelineState) -> None:
    """Create and run the PipelineEngine, handling checkpoint pauses and errors."""
    from agentic_dev.agents.registry import AgentRegistry  # noqa: WPS433
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.config import AGENT_DEFINITIONS_DIR, PROMPT_TEMPLATES_DIR  # noqa: WPS433
    from agentic_dev.orchestrator.engine import PipelineEngine  # noqa: WPS433
    from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

    checkpoint_config = _load_config(project_dir)
    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    claude = ClaudeRunner(log_dir=log_dir)
    registry = AgentRegistry(definitions_dir=AGENT_DEFINITIONS_DIR)
    doc_store = DocumentStore(project_dir)
    prompt_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)
    state_manager = StateManager(project_dir)

    engine = PipelineEngine(
        project_dir=project_dir,
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        state_manager=state_manager,
        checkpoint_config=checkpoint_config,
    )

    try:
        asyncio.run(engine.run())
        console.print("[bold green]Pipeline completed successfully.[/bold green]")
    except CheckpointPause:
        current_state = state_manager.load()
        _display_checkpoint(current_state, project_dir)
    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def new(
    app_name: str = typer.Argument(help="Name of the application to create"),
    path: str | None = typer.Option(None, help="Directory to create the project in"),
    from_figma: str | None = typer.Option(None, help="Figma URL to import designs from"),
    from_codebase: str | None = typer.Option(
        None, help="Path to existing codebase to onboard"
    ),
) -> None:
    """Create a new project and start the development pipeline."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.create_project(app_name)
        console.print(f"[green]Created project workspace at {project_dir}[/green]")

        # Initialise git repos in frontend/ and backend/
        from agentic_dev.workspace.git import init_repo  # noqa: WPS433

        asyncio.run(init_repo(project_dir / "frontend"))
        asyncio.run(init_repo(project_dir / "backend"))

        # Save initial pipeline state
        state_mgr = StateManager(project_dir)
        state = state_mgr.create_initial(app_name)

        # Save default checkpoint config
        _save_config(project_dir, CheckpointConfig())

        # Collect user requirements
        user_input = _collect_user_requirements()

        if from_codebase:
            from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
            from agentic_dev.onboarding.analyzer import analyze_codebase  # noqa: WPS433

            console.print(f"[cyan]Analyzing existing codebase: {from_codebase}[/cyan]")
            codebase_result = asyncio.run(
                analyze_codebase(ClaudeRunner(), Path(from_codebase))
            )
            user_input = (user_input or "") + "\n\n" + codebase_result.text

        if from_figma:
            from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
            from agentic_dev.onboarding.figma import analyze_figma_design  # noqa: WPS433

            console.print(f"[cyan]Importing designs from Figma: {from_figma}[/cyan]")
            figma_result = asyncio.run(
                analyze_figma_design(ClaudeRunner(), from_figma, project_dir)
            )
            user_input = (user_input or "") + "\n\n" + figma_result.text

        if not user_input:
            console.print("[bold red]No requirements provided. Aborting.[/bold red]")
            raise typer.Exit(code=1)

        # Save user input to docs/
        doc_store = DocumentStore(project_dir)
        doc_store.write("user_input", user_input)
        console.print("[green]Saved requirements to docs/user_input.md[/green]")

        _run_pipeline(project_dir, state)

    except (AgenticDevError, RuntimeError) as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def resume(
    app_name: str | None = typer.Argument(None, help="Name of the application to resume"),
    feedback: str | None = typer.Option(None, help="Feedback to inject into the next agent"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Resume a paused or failed pipeline."""
    if not app_name:
        console.print("[bold red]Please provide an application name.[/bold red]")
        raise typer.Exit(code=1)

    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if state.phase == PipelinePhase.FAILED:
            from agentic_dev.state.transitions import resume_from_failure  # noqa: WPS433

            state = resume_from_failure(state)
            state_mgr.save(state)
            console.print(
                f"[yellow]Recovering from failure. Restarting at phase: {state.phase}[/yellow]"
            )

        if feedback:
            state.checkpoint_feedback = feedback
            state_mgr.save(state)
            console.print("[cyan]Feedback injected into pipeline state.[/cyan]")

        console.print(f"[green]Resuming project: {app_name}[/green]")
        _run_pipeline(project_dir, state)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def update(
    app_name: str = typer.Argument(help="Name of the application to update"),
    change_request: str | None = typer.Option(None, help="Targeted change description"),
    full_spec: str | None = typer.Option(None, help="Path to full updated spec file"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Trigger an update cycle on an existing project."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if state.phase != PipelinePhase.COMPLETE:
            console.print(
                "[bold red]Project must be in COMPLETE state to update. "
                f"Current phase: {state.phase}[/bold red]"
            )
            raise typer.Exit(code=1)

        doc_store = DocumentStore(project_dir)

        if change_request:
            doc_store.write("change_request.md", change_request)
            console.print("[green]Saved change request to docs/change_request.md[/green]")
        elif full_spec:
            spec_path = Path(full_spec)
            if not spec_path.exists():
                console.print(f"[bold red]Spec file not found: {full_spec}[/bold red]")
                raise typer.Exit(code=1)
            new_input = spec_path.read_text(encoding="utf-8")
            doc_store.write("user_input.md", new_input)
            console.print("[green]Saved updated spec to docs/user_input.md[/green]")
        else:
            console.print(
                "[bold red]Provide --change-request or --full-spec.[/bold red]"
            )
            raise typer.Exit(code=1)

        # Determine restart phase using document diff
        from agentic_dev.documents.diff import diff_structured_input  # noqa: WPS433

        restart_phase = "FEATURE_ANALYSIS"
        if full_spec and doc_store.exists("structured_input.md"):
            old_input = doc_store.read("structured_input.md")
            new_input_text = doc_store.read("user_input.md")
            diff_result = diff_structured_input(old_input, new_input_text)
            restart_phase = diff_result.restart_from.upper()

        state.mode = "update"
        state.phase = PipelinePhase(restart_phase)
        state.error = None
        state_mgr.save(state)

        console.print(f"[cyan]Restarting pipeline from {restart_phase}[/cyan]")
        _run_pipeline(project_dir, state)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def status(
    app_name: str | None = typer.Argument(None, help="Name of the application"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Show pipeline status: current phase, sprint progress, costs."""
    if not app_name:
        console.print("[bold red]Please provide an application name.[/bold red]")
        raise typer.Exit(code=1)

    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        _display_status(state)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def config(
    app_name: str = typer.Argument(help="Name of the application"),
    checkpoints: str | None = typer.Option(None, help="Comma-separated checkpoint names to enable"),
    autonomy: str | None = typer.Option(None, help="Autonomy level: full, default, or maximum"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Configure checkpoint behavior for a project."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        if autonomy:
            cfg = from_autonomy_level(autonomy)
        else:
            cfg = _load_config(project_dir)

        if checkpoints:
            checkpoint_names = [c.strip() for c in checkpoints.split(",")]
            cfg.after_design = "after_design" in checkpoint_names
            cfg.after_each_sprint = "after_each_sprint" in checkpoint_names
            cfg.before_uat = "before_uat" in checkpoint_names

        _save_config(project_dir, cfg)
        console.print(f"[green]Configuration updated for {app_name}:[/green]")
        console.print(f"  after_design: {cfg.after_design}")
        console.print(f"  after_each_sprint: {cfg.after_each_sprint}")
        console.print(f"  before_uat: {cfg.before_uat}")

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def logs(
    app_name: str = typer.Argument(help="Name of the application"),
    agent: str | None = typer.Option(None, help="Filter by agent name"),
    sprint: int | None = typer.Option(None, help="Filter by sprint number"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """View agent run logs."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        logs_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
        if not logs_dir.exists() or not list(logs_dir.iterdir()):
            console.print("[yellow]No log files found.[/yellow]")
            return

        log_files = sorted(logs_dir.glob("*.log"))

        if agent:
            log_files = [f for f in log_files if agent in f.name]
        if sprint is not None:
            log_files = [f for f in log_files if f"sprint-{sprint}" in f.name]

        if not log_files:
            console.print("[yellow]No matching log files found.[/yellow]")
            return

        for log_file in log_files:
            console.print(Panel(
                log_file.read_text(encoding="utf-8"),
                title=log_file.name,
                border_style="blue",
            ))

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def cost(
    app_name: str = typer.Argument(help="Name of the application"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Show cost breakdown by agent and sprint."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if not state.agent_runs:
            console.print("[yellow]No agent runs recorded yet.[/yellow]")
            return

        table = Table(title=f"Cost Breakdown: {state.project_name}")
        table.add_column("Agent", style="bold")
        table.add_column("Phase")
        table.add_column("Sprint", justify="right")
        table.add_column("Cost (USD)", justify="right")
        table.add_column("Status")

        # Group runs by sprint for visual clarity
        design_runs = [r for r in state.agent_runs if r.sprint is None]
        sprint_runs = [r for r in state.agent_runs if r.sprint is not None]

        for run in design_runs:
            status = "[green]ok[/green]" if run.success else "[red]failed[/red]"
            table.add_row(run.agent_name, run.phase, "-", f"${run.cost_usd:.4f}", status)

        for run in sorted(sprint_runs, key=lambda r: (r.sprint or 0, r.started_at)):
            status = "[green]ok[/green]" if run.success else "[red]failed[/red]"
            table.add_row(
                run.agent_name,
                run.phase,
                str(run.sprint),
                f"${run.cost_usd:.4f}",
                status,
            )

        table.add_section()
        table.add_row("", "", "[bold]Total[/bold]", f"[bold]${state.total_cost_usd:.4f}[/bold]", "")

        console.print(table)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)
