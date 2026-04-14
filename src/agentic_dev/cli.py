"""CLI entry point for the agentic-dev agency."""

import asyncio
import json
import sys
import traceback
from datetime import datetime, timezone
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
    LATEST_SYMLINK,
    LOGS_DIR,
    RUNS_DIR,
)
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgenticDevError, CheckpointPause, GracefulShutdown
from agentic_dev.orchestrator.checkpoint import CheckpointConfig, from_autonomy_level
from agentic_dev.mcp.setup import check_mcp_prerequisites
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelinePhase, PipelineState, SprintState, SprintStatus
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

    if state.remediation_cycle > 0:
        table.add_row("Remediation Cycle", str(state.remediation_cycle))

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
        line = input()
        if line == "":
            empty_count += 1
            if empty_count >= 2:
                break
            lines.append("")
        else:
            empty_count = 0
            lines.append(line)

    return "\n".join(lines).strip()


def _read_requirements_file(file_path: str) -> str:
    """Read and validate a requirements file, exiting on error."""
    path = Path(file_path)
    if not path.exists():
        console.print(f"[bold red]Requirements file not found: {file_path}[/bold red]")
        raise typer.Exit(code=1)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        console.print(f"[bold red]Cannot read file {file_path}: {exc}[/bold red]")
        raise typer.Exit(code=1)
    if not content.strip():
        console.print(f"[bold red]Requirements file is empty: {file_path}[/bold red]")
        raise typer.Exit(code=1)
    return content.strip()


def _run_pipeline(project_dir: Path, state: PipelineState) -> None:
    """Create and run the PipelineEngine, handling checkpoint pauses and errors."""
    from agentic_dev.agents.registry import AgentRegistry  # noqa: WPS433
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.config import AGENT_DEFINITIONS_DIR, PROMPT_TEMPLATES_DIR  # noqa: WPS433
    from agentic_dev.logging import (  # noqa: WPS433
        emit,
        generate_run_id,
        get_event_logger,
        setup_logging,
        teardown_logging,
    )
    from agentic_dev.logging.events import (  # noqa: WPS433
        PipelineCheckpointEvent,
        PipelineCompleteEvent,
        PipelineFailedEvent,
        PipelineStartEvent,
    )
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

    run_id = generate_run_id()
    _event_log = get_event_logger("pipeline")
    setup_logging(run_id, state.project_name, log_dir, console)

    from datetime import datetime, timezone  # noqa: WPS433

    start_time = datetime.now(timezone.utc)

    emit(_event_log, PipelineStartEvent(
        mode=state.mode,
        phase=str(state.phase),
        command_args={},
        message=f"Pipeline started (mode={state.mode}, phase={state.phase})",
    ))

    try:
        asyncio.run(engine.run())
        duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
        current_state = state_manager.load()
        emit(_event_log, PipelineCompleteEvent(
            total_cost_usd=current_state.total_cost_usd,
            total_duration_s=duration_s,
            sprint_count=len(current_state.sprints),
            message=f"Pipeline complete (${current_state.total_cost_usd:.4f}, {duration_s:.1f}s)",
        ))
        teardown_logging()
        console.print("[bold green]Pipeline completed successfully.[/bold green]")
    except CheckpointPause:
        current_state = state_manager.load()
        docs = doc_store.list_documents()
        emit(_event_log, PipelineCheckpointEvent(
            phase=str(current_state.phase),
            total_cost_usd=current_state.total_cost_usd,
            documents_produced=docs,
            message=f"Pipeline paused at checkpoint ({current_state.phase})",
        ))
        teardown_logging()
        _display_checkpoint(current_state, project_dir)
    except GracefulShutdown:
        current_state = state_manager.load()
        teardown_logging()
        console.print("[yellow]Shutdown requested. State saved.[/yellow]")
        console.print(
            f"  Resume with: agentic-dev resume {current_state.project_name}"
        )
    except AgenticDevError as exc:
        duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
        emit(_event_log, PipelineFailedEvent(
            error=str(exc),
            failed_at_phase=str(state.phase),
            traceback=traceback.format_exc(),
            level="ERROR",
            message=f"Pipeline failed at {state.phase}: {exc}",
        ))
        teardown_logging()
        _display_error(exc)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def new(
    app_name: str = typer.Argument(help="Name of the application to create"),
    path: str | None = typer.Option(None, help="Directory to create the project in"),
    from_file: str | None = typer.Option(
        None, "--from-file", help="Path to a file containing project requirements"
    ),
    from_figma: list[str] | None = typer.Option(
        None, help="Figma URL to import designs from (use '::' for annotation, repeatable)"
    ),
    from_codebase: list[str] | None = typer.Option(
        None, help="Existing codebase to use as reference context (use '::' for annotation, repeatable)"
    ),
) -> None:
    """Create a new project and start the development pipeline."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.create_project(app_name)
        console.print(f"[green]Created project workspace at {project_dir}[/green]")

        # Save initial pipeline state
        state_mgr = StateManager(project_dir)
        state = state_mgr.create_initial(app_name)

        # Save default checkpoint config
        _save_config(project_dir, CheckpointConfig())

        # Collect user requirements
        if from_file:
            user_input = _read_requirements_file(from_file)
        else:
            user_input = _collect_user_requirements()

        from agentic_dev.onboarding.models import AnnotatedSource  # noqa: WPS433

        codebase_sources = [AnnotatedSource.parse(s) for s in (from_codebase or [])]
        figma_sources = [AnnotatedSource.parse(s) for s in (from_figma or [])]

        if codebase_sources:
            from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
            from agentic_dev.onboarding.analyzer import analyze_codebases  # noqa: WPS433

            for src in codebase_sources:
                label = f"{src.value} ({src.annotation})" if src.annotation else src.value
                console.print(f"[cyan]Analyzing existing codebase: {label}[/cyan]")

            codebase_results = asyncio.run(
                analyze_codebases(ClaudeRunner(), codebase_sources)
            )
            for src, result in zip(codebase_sources, codebase_results):
                header = "\n\n---\n## Source: Codebase"
                if src.annotation:
                    header += f" - {src.annotation}"
                header += f"\n**Path:** `{src.value}`\n\n"
                user_input = (user_input or "") + header + result.text

        if figma_sources:
            from agentic_dev.mcp.setup import check_mcp_prerequisites  # noqa: WPS433
            from agentic_dev.onboarding.figma import write_figma_sources  # noqa: WPS433

            if not check_mcp_prerequisites(["figma"], console):
                raise typer.Exit(code=1)

            for src in figma_sources:
                label = f"{src.value} ({src.annotation})" if src.annotation else src.value
                console.print(f"[cyan]Registered Figma source: {label}[/cyan]")

        if not user_input and not figma_sources:
            console.print("[bold red]No requirements provided. Aborting.[/bold red]")
            raise typer.Exit(code=1)

        # Save user input to docs/
        doc_store = DocumentStore(project_dir)
        if user_input:
            doc_store.write("user_input", user_input)
            console.print("[green]Saved requirements to docs/user_input.md[/green]")

        if figma_sources:
            write_figma_sources(doc_store, figma_sources)

        _run_pipeline(project_dir, state)

    except (AgenticDevError, RuntimeError) as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def resume(
    app_name: str | None = typer.Argument(None, help="Name of the application to resume"),
    feedback: str | None = typer.Option(None, help="Feedback to inject into the next agent"),
    skip_sprint: int | None = typer.Option(
        None, "--skip-sprint", help="Skip the given sprint number (mark as complete)"
    ),
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

        if skip_sprint is not None:
            matched = [s for s in state.sprints if s.sprint_number == skip_sprint]
            if not matched:
                console.print(
                    f"[bold red]Sprint {skip_sprint} not found.[/bold red]"
                )
                raise typer.Exit(code=1)
            for sprint in matched:
                sprint.status = SprintStatus.COMPLETE
                sprint.completed_at = datetime.now(timezone.utc)
            state_mgr.save(state)
            console.print(f"[yellow]Skipped sprint {skip_sprint} (marked as complete).[/yellow]")

        if feedback:
            state.checkpoint_feedback = feedback
            state_mgr.save(state)
            console.print("[cyan]Feedback injected into pipeline state.[/cyan]")

        console.print(f"[green]Resuming project: {app_name}[/green]")
        _run_pipeline(project_dir, state)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


def _start_update_cycle(
    project_dir: Path,
    state: PipelineState,
    state_mgr: StateManager,
    change_input: str | None,
    mode: str,
    restart_phase: PipelinePhase,
    is_targeted: bool = False,
    design_changes: str | None = None,
    spec_changes: str | None = None,
) -> None:
    """Archive docs, write change input, reset state, and run the pipeline.

    Shared by the ``update`` and ``remediate`` commands.

    When *is_targeted* is True the change input describes incremental changes
    rather than a full replacement.  A ``change_request`` document is written
    so the pipeline engine can merge it into the existing structured input.

    When *design_changes* is provided (a change summary produced by the
    design change detection agent), it is written as a ``design_changes``
    document so downstream agents know what changed.

    When *spec_changes* is provided (a diff summary produced by the
    ``spec_diff`` agent for ``--full-spec`` updates), it is written as a
    ``change_request`` document so downstream agents know what changed.
    """
    from agentic_dev.state.transitions import reset_for_update  # noqa: WPS433

    doc_store = DocumentStore(project_dir)

    if change_input:
        doc_store.write("user_input", change_input)
        if is_targeted:
            doc_store.write("change_request", change_input)

    if design_changes:
        doc_store.write("design_changes", design_changes)

    if spec_changes:
        doc_store.write("spec_changes", spec_changes)

    state = reset_for_update(state, restart_phase, mode)
    state_mgr.save(state)

    console.print(f"[cyan]Restarting pipeline from {restart_phase}[/cyan]")
    _run_pipeline(project_dir, state)


@app.command()
def update(
    app_name: str = typer.Argument(help="Name of the application to update"),
    full_spec: str | None = typer.Option(None, help="Path to full updated spec file"),
    from_file: str | None = typer.Option(
        None, "--from-file", help="Path to a file containing change requirements"
    ),
    from_figma: list[str] | None = typer.Option(
        None, help="Figma URL to import designs from (use '::' for annotation, repeatable)"
    ),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Trigger an update cycle on an existing project."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if state.phase not in {PipelinePhase.COMPLETE, PipelinePhase.ADOPTED}:
            console.print(
                "[bold red]Project must be in COMPLETE or ADOPTED state to update. "
                f"Current phase: {state.phase}[/bold red]"
            )
            raise typer.Exit(code=1)

        doc_store = DocumentStore(project_dir)

        if full_spec and from_file:
            console.print(
                "[bold red]Cannot use both --full-spec and --from-file. "
                "Please provide only one.[/bold red]"
            )
            raise typer.Exit(code=1)

        # -- Text channel --
        change_input: str | None = None
        spec_changes: str | None = None
        if full_spec:
            spec_path = Path(full_spec)
            if not spec_path.exists():
                console.print(f"[bold red]Spec file not found: {full_spec}[/bold red]")
                raise typer.Exit(code=1)
            change_input = spec_path.read_text(encoding="utf-8")

            # Read old structured_input before it gets overwritten for diff
            old_structured_input = (
                doc_store.read("structured_input")
                if doc_store.exists("structured_input")
                else ""
            )
            if old_structured_input:
                from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
                from agentic_dev.documents.diff import run_spec_diff  # noqa: WPS433

                console.print("[cyan]Comparing old and new specs...[/cyan]")
                log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / "logs"
                spec_changes = asyncio.run(
                    run_spec_diff(
                        ClaudeRunner(log_dir=log_dir),
                        old_structured_input, change_input, project_dir,
                    )
                )
        elif from_file:
            change_input = _read_requirements_file(from_file)
        elif not from_figma:
            change_input = _collect_user_requirements()
            if not change_input:
                console.print(
                    "[bold red]No change description provided.[/bold red]"
                )
                raise typer.Exit(code=1)
        else:
            change_input = _collect_user_requirements()
            # Figma is provided, so empty text is acceptable

        # -- Design channel --
        from agentic_dev.onboarding.models import AnnotatedSource  # noqa: WPS433

        figma_sources = [AnnotatedSource.parse(s) for s in (from_figma or [])]
        design_changes: str | None = None

        if figma_sources:
            from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
            from agentic_dev.mcp.setup import check_mcp_prerequisites  # noqa: WPS433
            from agentic_dev.onboarding.figma import detect_design_changes  # noqa: WPS433
            from agentic_dev.onboarding.figma import write_figma_sources  # noqa: WPS433

            if not check_mcp_prerequisites(["figma"], console):
                raise typer.Exit(code=1)

            write_figma_sources(doc_store, figma_sources)

            # Detect design changes by comparing live Figma against existing specs
            if doc_store.exists("frontend_spec"):
                existing_spec = doc_store.read("frontend_spec")
                log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / "logs"
                figma_claude = ClaudeRunner(log_dir=log_dir)

                console.print("[cyan]Detecting design changes against existing specs...[/cyan]")
                change_result = asyncio.run(
                    detect_design_changes(figma_claude, figma_sources, existing_spec, project_dir)
                )
                if change_result.has_changes:
                    design_changes = change_result.summary
                else:
                    console.print("[green]No design changes detected.[/green]")

        if not change_input and not figma_sources:
            console.print("[bold red]No change description provided.[/bold red]")
            raise typer.Exit(code=1)

        # Determine restart phase using document diff
        from agentic_dev.documents.diff import diff_structured_input  # noqa: WPS433

        is_targeted = not full_spec
        restart_phase = PipelinePhase.FEATURE_ANALYSIS
        if full_spec and doc_store.exists("structured_input.md"):
            old_input = doc_store.read("structured_input.md")
            diff_result = diff_structured_input(old_input, change_input or "")
            restart_phase = PipelinePhase(diff_result.restart_from.upper())
        elif figma_sources and not change_input:
            restart_phase = PipelinePhase.ARCHITECTURE

        _start_update_cycle(
            project_dir=project_dir,
            state=state,
            state_mgr=state_mgr,
            change_input=change_input or None,
            mode="update",
            restart_phase=restart_phase,
            is_targeted=is_targeted,
            design_changes=design_changes,
            spec_changes=spec_changes,
        )

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def remediate(
    app_name: str = typer.Argument(help="Name of the application to remediate"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Fix UAT failures by running a full remediation pipeline cycle."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if state.phase != PipelinePhase.COMPLETE:
            console.print(
                "[bold red]Project must be in COMPLETE state to remediate. "
                f"Current phase: {state.phase}[/bold red]"
            )
            raise typer.Exit(code=1)

        doc_store = DocumentStore(project_dir)

        if not doc_store.exists("uat_report"):
            console.print(
                "[bold red]No UAT report found. Run the pipeline to completion first.[/bold red]"
            )
            raise typer.Exit(code=1)

        uat_report = doc_store.read("uat_report")
        if not uat_report.strip():
            console.print("[bold red]UAT report is empty.[/bold red]")
            raise typer.Exit(code=1)

        from agentic_dev.orchestrator.uat_composer import compose_remediation_input  # noqa: WPS433

        change_input = compose_remediation_input(uat_report, app_name)

        console.print(
            f"[cyan]Starting remediation cycle {state.remediation_cycle + 1} "
            f"for {app_name}[/cyan]"
        )

        _start_update_cycle(
            project_dir=project_dir,
            state=state,
            state_mgr=state_mgr,
            change_input=change_input,
            mode="remediate",
            restart_phase=PipelinePhase.INPUT_PROCESSING,
        )

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

        doc_store = DocumentStore(project_dir)
        if doc_store.exists("sync_change_request"):
            console.print(
                "[yellow]Warning:[/yellow] Pending code changes from sync. "
                "Run 'agentic-dev update' to apply them, or delete "
                "docs/sync_change_request.md to dismiss."
            )

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
    run: str | None = typer.Option(None, help="Specific run ID to view"),
    jsonl: bool = typer.Option(False, "--jsonl", help="Show JSON lines instead of human-readable log"),
    agent: str | None = typer.Option(None, help="Filter agent dumps by agent name"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """View pipeline run logs or agent dumps."""
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        logs_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
        if not logs_dir.exists():
            console.print("[yellow]No log files found.[/yellow]")
            return

        # If --agent is specified, show agent dumps
        if agent:
            dumps_dir = logs_dir / "agent_dumps"
            if not dumps_dir.exists():
                console.print("[yellow]No agent dumps found.[/yellow]")
                return
            dump_files = sorted(dumps_dir.glob(f"*{agent}*.json"))
            if not dump_files:
                console.print(f"[yellow]No dumps found for agent '{agent}'.[/yellow]")
                return
            for dump_file in dump_files:
                console.print(Panel(
                    dump_file.read_text(encoding="utf-8"),
                    title=dump_file.name,
                    border_style="blue",
                ))
            return

        # Otherwise show pipeline run logs
        runs_dir = logs_dir / RUNS_DIR
        if run:
            run_dir = runs_dir / run
        else:
            latest = logs_dir / LATEST_SYMLINK
            if latest.is_symlink() or latest.exists():
                run_dir = latest.resolve()
            elif runs_dir.exists():
                run_dirs = sorted(runs_dir.iterdir())
                if not run_dirs:
                    console.print("[yellow]No pipeline runs found.[/yellow]")
                    return
                run_dir = run_dirs[-1]
            else:
                console.print("[yellow]No pipeline runs found.[/yellow]")
                return

        if not run_dir.exists():
            console.print(f"[yellow]Run directory not found: {run_dir}[/yellow]")
            return

        log_file = run_dir / ("events.jsonl" if jsonl else "pipeline.log")
        if not log_file.exists():
            console.print(f"[yellow]Log file not found: {log_file.name}[/yellow]")
            return

        run_id = run_dir.name
        console.print(Panel(
            log_file.read_text(encoding="utf-8"),
            title=f"Run {run_id} — {log_file.name}",
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


@app.command()
def adopt(
    project_path: str = typer.Argument(help="Path to the existing project to adopt"),
    from_figma: list[str] | None = typer.Option(
        None, help="Figma URL to import designs from (use '::' for annotation, repeatable)"
    ),
    extend: str | None = typer.Option(
        None, help="New requirements to add on top of the adopted project"
    ),
    frontend_dir: str | None = typer.Option(
        None, "--frontend", help="Explicit frontend directory name (skips auto-detection)"
    ),
    backend_dir: str | None = typer.Option(
        None, "--backend", help="Explicit backend directory name (skips auto-detection)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts"),
) -> None:
    """Adopt an existing project and reverse-engineer full specifications."""
    try:
        from agentic_dev.config import (  # noqa: WPS433
            DirectoryMap,
            ProjectConfig,
            save_project_config,
        )

        path = Path(project_path).resolve()
        app_name = path.name

        if not path.exists():
            console.print(f"[bold red]Path does not exist: {path}[/bold red]")
            raise typer.Exit(code=1)

        if (path / AGENTIC_DEV_METADATA_DIR).exists():
            console.print(
                f"[bold red]Project already has {AGENTIC_DEV_METADATA_DIR}/. "
                "Use 'sync' to update specs.[/bold red]"
            )
            raise typer.Exit(code=1)

        workspace_mgr = WorkspaceManager(base_dir=path.parent)
        workspace_mgr.adopt_project(path, app_name)
        console.print(f"[green]Initialized agentic-dev in {path}[/green]")

        if frontend_dir or backend_dir:
            directory_map = DirectoryMap(frontend=frontend_dir, backend=backend_dir)
            console.print(
                f"[cyan]Using explicit mapping: "
                f"frontend={frontend_dir}, backend={backend_dir}[/cyan]"
            )
        else:
            console.print("[cyan]Detecting project structure...[/cyan]")
            from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
            from agentic_dev.onboarding.structure_detector import detect_structure  # noqa: WPS433

            log_dir = path / AGENTIC_DEV_METADATA_DIR / "logs"
            claude = ClaudeRunner(log_dir=log_dir)
            directory_map = asyncio.run(detect_structure(claude, path))
            console.print(
                f"[green]Detected: frontend={directory_map.frontend}, "
                f"backend={directory_map.backend}[/green]"
            )

        from agentic_dev.state.models import ProjectType  # noqa: WPS433

        if directory_map.frontend and directory_map.backend:
            project_type = ProjectType.FULLSTACK
        elif directory_map.frontend:
            project_type = ProjectType.FRONTEND_ONLY
        else:
            project_type = ProjectType.BACKEND_ONLY

        console.print(f"[cyan]Project type: {project_type.value}[/cyan]")

        if not yes:
            console.print(
                "\n[yellow]Adoption runs multiple AI agents to reverse-engineer specs.\n"
                "Estimated cost: $20-50 depending on codebase size.[/yellow]"
            )
            confirm = Prompt.ask("Proceed?", choices=["y", "n"], default="y")
            if confirm != "y":
                console.print("[dim]Aborted.[/dim]")
                raise typer.Exit(code=0)

        config = ProjectConfig(
            app_name=app_name,
            directory_map=directory_map,
        )
        save_project_config(path, config)

        state_mgr = StateManager(path)
        state = PipelineState(
            project_name=app_name,
            project_type=project_type,
            phase=PipelinePhase.ADOPTING,
            mode="adopt",
            origin="adopted",
        )
        state_mgr.save(state)

        from agentic_dev.onboarding.models import AnnotatedSource  # noqa: WPS433

        figma_sources = [AnnotatedSource.parse(s) for s in (from_figma or [])]

        if figma_sources:
            from agentic_dev.mcp.setup import check_mcp_prerequisites  # noqa: WPS433
            from agentic_dev.onboarding.figma import write_figma_sources  # noqa: WPS433

            if not check_mcp_prerequisites(["figma"], console):
                raise typer.Exit(code=1)

            for src in figma_sources:
                label = f"{src.value} ({src.annotation})" if src.annotation else src.value
                console.print(f"[cyan]Registered Figma source: {label}[/cyan]")

        console.print("\n[bold cyan]Running spec reverse-engineering...[/bold cyan]")

        from agentic_dev.agents.registry import AgentRegistry  # noqa: WPS433
        from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
        from agentic_dev.config import AGENT_DEFINITIONS_DIR, PROMPT_TEMPLATES_DIR  # noqa: WPS433
        from agentic_dev.orchestrator.adoption import run_adoption  # noqa: WPS433
        from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

        log_dir = path / AGENTIC_DEV_METADATA_DIR / "logs"
        claude = ClaudeRunner(log_dir=log_dir)
        registry = AgentRegistry(definitions_dir=AGENT_DEFINITIONS_DIR)
        doc_store = DocumentStore(path)

        if figma_sources:
            write_figma_sources(doc_store, figma_sources)
        prompt_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)

        adoption_result = asyncio.run(run_adoption(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=path,
            directory_map=directory_map,
            project_type=project_type,
        ))

        state = state_mgr.load()
        state.total_cost_usd += adoption_result.total_cost

        if extend:
            user_input = extend
            if doc_store.exists("structured_input"):
                user_input = doc_store.read("structured_input") + "\n\n---\n\n" + extend
            doc_store.write("user_input", user_input)

            from agentic_dev.state.transitions import advance_phase  # noqa: WPS433

            state = advance_phase(state, PipelinePhase.INPUT_PROCESSING)
            state_mgr.save(state)
            console.print("[cyan]Extending with new requirements...[/cyan]")
            _run_pipeline(path, state)
        else:
            from agentic_dev.state.transitions import advance_phase  # noqa: WPS433

            state = advance_phase(state, PipelinePhase.ADOPTED)
            state.last_sync_at = datetime.now(timezone.utc)
            state_mgr.save(state)

            console.print(
                f"\n[bold green]Project adopted successfully![/bold green]\n"
                f"  Features extracted: {adoption_result.features_count}\n"
                f"  Endpoints mapped: {adoption_result.endpoints_count}\n"
                f"  Documents: {', '.join(adoption_result.documents_produced)}\n"
                f"  Cost: ${adoption_result.total_cost:.4f}\n"
                f"\nSpecs saved to {path / 'docs'}/"
            )

    except (AgenticDevError, RuntimeError) as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def sync(
    app_name: str | None = typer.Argument(None, help="Name of the application to sync"),
    from_source: str | None = typer.Option(
        None, "--from", help="Source of truth: code, specs, or figma"
    ),
    scope: str | None = typer.Option(
        None, help="Sync scope: api, frontend, or backend"
    ),
    check: bool = typer.Option(False, "--check", help="Check-only mode: report drift without changes"),
    path: str | None = typer.Option(None, help="Directory containing the project"),
) -> None:
    """Detect drift between code, specs, and Figma designs, and resolve interactively."""
    try:
        from agentic_dev.config import (  # noqa: WPS433
            load_project_config,
            save_project_config,
        )

        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name or "")

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        terminal_phases = (PipelinePhase.COMPLETE, PipelinePhase.ADOPTED)
        if state.phase not in terminal_phases:
            console.print(
                "[bold red]Project must be in COMPLETE or ADOPTED state to sync. "
                f"Current phase: {state.phase}[/bold red]"
            )
            raise typer.Exit(code=1)

        config = load_project_config(project_dir)
        sync_scope = scope or "all"

        console.print(f"[cyan]Running drift detection (scope: {sync_scope})...[/cyan]")

        from agentic_dev.agents.registry import AgentRegistry  # noqa: WPS433
        from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
        from agentic_dev.config import AGENT_DEFINITIONS_DIR, PROMPT_TEMPLATES_DIR  # noqa: WPS433
        from agentic_dev.orchestrator.sync import apply_sync_resolutions, run_sync  # noqa: WPS433
        from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

        log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / "logs"
        claude = ClaudeRunner(log_dir=log_dir)
        registry = AgentRegistry(definitions_dir=AGENT_DEFINITIONS_DIR)
        doc_store = DocumentStore(project_dir)
        prompt_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)

        report = asyncio.run(run_sync(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            directory_map=config.directory_map,
            scope=sync_scope,
            sync_ignores=config.sync_ignores,
        ))

        if not report.items:
            console.print("[bold green]No drift detected. Code and specs are in sync.[/bold green]")
            state.last_sync_at = datetime.now(timezone.utc)
            state_mgr.save(state)
            return

        # Display report
        console.print(f"\n[bold]Sync Report:[/bold] {report.summary}\n")
        for item in report.items:
            console.print(f"  {item.id} \\[{item.scope}] ({item.category}): {item.description}")

        if check:
            console.print(f"\n[dim]{len(report.items)} drift item(s) found. Run without --check to resolve.[/dim]")
            return

        # Resolve items
        if from_source == "code":
            for item in report.items:
                item.resolution = "to_spec"
            console.print("\n[cyan]Auto-resolving all items as 'to_spec' (code is truth)[/cyan]")
        elif from_source == "specs":
            for item in report.items:
                item.resolution = "to_code"
            console.print("\n[cyan]Auto-resolving all items as 'to_code' (specs are truth)[/cyan]")
        elif from_source == "figma":
            _figma_to_code = {"in_spec_not_code", "difference"}
            _figma_to_spec = {"design_drift", "in_code_not_spec"}
            for item in report.items:
                if item.scope == "figma":
                    if item.category in _figma_to_code:
                        item.resolution = "to_code"
                    elif item.category in _figma_to_spec:
                        item.resolution = "to_spec"
            unresolved = [i for i in report.items if i.resolution is None]
            if unresolved:
                _resolve_items_interactively(unresolved)
        else:
            _resolve_items_interactively(report.items)

        # Snapshot spec line counts before applying resolutions
        spec_line_counts_before: dict[str, int] = {}
        for spec_name in ("frontend_spec", "backend_spec", "api_contract"):
            if doc_store.exists(spec_name):
                spec_line_counts_before[spec_name] = len(doc_store.read(spec_name).splitlines())

        # Apply resolutions
        apply_result = asyncio.run(apply_sync_resolutions(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            project_dir=project_dir,
            report=report,
        ))

        # Save ignore items to config
        new_ignores = [i.id for i in report.items if i.resolution == "ignore"]
        if new_ignores:
            config.sync_ignores.extend(new_ignores)
            save_project_config(project_dir, config)

        state.last_sync_at = datetime.now(timezone.utc)
        state.total_cost_usd += apply_result.total_cost
        state_mgr.save(state)

        console.print(
            f"\n[bold green]Sync complete![/bold green]\n"
            f"  Specs updated: {apply_result.specs_updated}\n"
            f"  Code changes queued: {apply_result.code_changes_queued}\n"
            f"  Ignored: {apply_result.items_ignored}\n"
            f"  Deferred: {apply_result.items_deferred}"
        )

        if apply_result.specs_updated > 0:
            console.print("\n[bold]Spec changes:[/bold]")
            for spec_name in ("frontend_spec", "backend_spec", "api_contract"):
                if not doc_store.exists(spec_name):
                    continue
                after = len(doc_store.read(spec_name).splitlines())
                before = spec_line_counts_before.get(spec_name, 0)
                delta = after - before
                if delta == 0 and spec_name not in spec_line_counts_before:
                    continue
                sign = f"+{delta}" if delta > 0 else str(delta)
                console.print(f"  {spec_name}: {before} -> {after} lines ({sign})")

        if apply_result.code_changes_queued > 0:
            console.print(
                f"\n[yellow]{apply_result.code_changes_queued} code change(s) queued. "
                f"Run 'agentic-dev update {app_name} --from-sync' to apply them.[/yellow]"
            )

    except (AgenticDevError, RuntimeError) as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


def _resolve_items_interactively(items: list) -> None:
    """Prompt the user to resolve each drift item."""
    for item in items:
        if item.resolution is not None:
            continue
        console.print(f"\n  [bold]{item.id}[/bold] [{item.scope}] {item.description}")
        choice = Prompt.ask(
            "  Resolution",
            choices=["to_spec", "to_code", "ignore", "defer"],
            default="to_spec",
        )
        item.resolution = choice


# ---------------------------------------------------------------------------
# Integration statuses that indicate a crashed/stuck integration run
# ---------------------------------------------------------------------------
_INTEGRATION_STUCK_STATUSES = frozenset({
    SprintStatus.INTEGRATION,
    SprintStatus.INTEGRATION_QA,
    SprintStatus.INTEGRATION_CORRECTION,
})


def _qualify_sprints(
    sprints: list[SprintState],
    sprint_number: int | None,
    force: bool,
) -> list[SprintState]:
    """Filter sprints that qualify for integration reruns.

    Qualifying means the sprint has integration_services AND one of:
    - Stuck mid-integration (crashed previous run)
    - Never integrated (no integration_session_id)
    - force=True (rerun even if already integrated)
    """
    candidates = [s for s in sprints if s.integration_services]
    if sprint_number is not None:
        candidates = [s for s in candidates if s.sprint_number == sprint_number]

    qualifying: list[SprintState] = []
    for sprint in candidates:
        if sprint.status in _INTEGRATION_STUCK_STATUSES:
            qualifying.append(sprint)
        elif sprint.integration_session_id is None:
            qualifying.append(sprint)
        elif force:
            qualifying.append(sprint)

    return qualifying


def _run_integration(
    project_dir: Path,
    state: PipelineState,
    sprints: list[SprintState],
) -> None:
    """Run integration for qualifying sprints using IntegrationRunner."""
    from agentic_dev.agents.registry import AgentRegistry  # noqa: WPS433
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.config import AGENT_DEFINITIONS_DIR, PROMPT_TEMPLATES_DIR  # noqa: WPS433
    from agentic_dev.logging import (  # noqa: WPS433
        emit,
        generate_run_id,
        get_event_logger,
        setup_logging,
        teardown_logging,
    )
    from agentic_dev.logging.events import PipelineStartEvent  # noqa: WPS433
    from agentic_dev.orchestrator.integration_runner import IntegrationRunner  # noqa: WPS433
    from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    claude = ClaudeRunner(log_dir=log_dir)
    registry = AgentRegistry(definitions_dir=AGENT_DEFINITIONS_DIR)
    doc_store = DocumentStore(project_dir)
    prompt_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)
    state_manager = StateManager(project_dir)

    runner = IntegrationRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        state_manager=state_manager,
        pipeline_state=state,
    )

    run_id = generate_run_id()
    _event_log = get_event_logger("integrate")
    setup_logging(run_id, state.project_name, log_dir, console)

    start_time = datetime.now(timezone.utc)

    emit(_event_log, PipelineStartEvent(
        mode="integrate",
        phase=str(state.phase),
        command_args={},
        message=f"Integration started for {len(sprints)} sprint(s)",
    ))

    try:
        total_cost = 0.0
        for sprint in sprints:
            resuming = sprint.status in _INTEGRATION_STUCK_STATUSES
            label = "Resuming" if resuming else "Running"
            console.print(
                f"[cyan]{label} integration for sprint {sprint.sprint_number}: "
                f"{sprint.name}[/cyan]"
            )
            result = asyncio.run(runner.run_integration(sprint))
            total_cost += result.total_cost
            state.total_cost_usd += result.total_cost
            state_manager.save(state)
            console.print(
                f"  [green]Sprint {sprint.sprint_number} integration complete "
                f"(${result.total_cost:.4f})[/green]"
            )

        duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
        teardown_logging()

        console.print()
        console.print(
            f"[bold green]Integration complete![/bold green]\n"
            f"  Sprints integrated: {len(sprints)}\n"
            f"  Cost: ${total_cost:.4f}\n"
            f"  Duration: {duration_s:.1f}s"
        )
    except AgenticDevError as exc:
        teardown_logging()
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def integrate(
    app_name: str = typer.Argument(help="Name of the application"),
    sprint_number: int | None = typer.Option(
        None, "--sprint", help="Specific sprint number to integrate",
    ),
    path: str | None = typer.Option(None, help="Directory containing the project"),
    force: bool = typer.Option(
        False, "--force", help="Rerun integration even if already completed",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompts",
    ),
) -> None:
    """Rerun integration stages with properly configured MCP services.

    Use this after completing a pipeline run where MCP services were not
    configured. Configure the services first, then run this command to
    integrate them into the relevant sprints.
    """
    try:
        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.get_project_dir(app_name)

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        terminal_phases = (PipelinePhase.COMPLETE, PipelinePhase.ADOPTED)
        if state.phase not in terminal_phases:
            console.print(
                "[bold red]Project must be in COMPLETE or ADOPTED state to integrate. "
                f"Current phase: {state.phase}[/bold red]"
            )
            raise typer.Exit(code=1)

        qualifying = _qualify_sprints(state.sprints, sprint_number, force)

        if not qualifying:
            console.print(
                "[bold yellow]No qualifying sprints found for integration.[/bold yellow]\n"
                "  Sprints must have integration services defined and not already be integrated.\n"
                "  Use --force to rerun integration for already-integrated sprints."
            )
            raise typer.Exit(code=1)

        # Display qualifying sprints
        table = Table(title="Sprints to Integrate")
        table.add_column("Sprint", style="bold")
        table.add_column("Name")
        table.add_column("Services")
        table.add_column("Status")

        for sprint in qualifying:
            if sprint.status in _INTEGRATION_STUCK_STATUSES:
                status_label = f"[yellow]Resuming ({sprint.status})[/yellow]"
            elif sprint.integration_session_id is not None:
                status_label = "[cyan]Rerun (--force)[/cyan]"
            else:
                status_label = "[green]Fresh[/green]"
            table.add_row(
                str(sprint.sprint_number),
                sprint.name,
                ", ".join(sprint.integration_services),
                status_label,
            )
        console.print(table)

        # Validate MCP readiness
        all_services = sorted({
            s for sprint in qualifying for s in sprint.integration_services
        })
        if not check_mcp_prerequisites(all_services, console, project_dir):
            console.print(
                "\n[bold red]MCP services not fully configured. "
                "Please configure them and retry.[/bold red]"
            )
            raise typer.Exit(code=1)

        if not yes:
            typer.confirm(
                f"\nProceed with integration for {len(qualifying)} sprint(s)?",
                abort=True,
            )

        _run_integration(project_dir, state, qualifying)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)
