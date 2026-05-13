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
from rich.table import Table

from agentic_dev.config import (
    AGENTIC_DEV_METADATA_DIR,
    CONFIG_FILE,
    DEFAULT_PROJECTS_DIR,
    LATEST_SYMLINK,
    LOGS_DIR,
    MAX_CONSECUTIVE_RATE_LIMIT_PAUSES,
    RATE_LIMIT_PAUSE_POLL_INTERVAL_SECONDS,
    RUNS_DIR,
)
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import (
    AgenticDevError,
    CheckpointPause,
    GracefulShutdown,
    RateLimitPause,
)
from agentic_dev.orchestrator.checkpoint import CheckpointConfig, from_autonomy_level
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelinePhase, PipelineState, SprintStatus
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


def _display_rate_limit_pause(pause: RateLimitPause) -> None:
    """Render a Rich banner describing the pending rate-limit pause."""
    detail = (
        f"[bold]Phase:[/bold] {pause.phase}\n"
        f"[bold]Wait:[/bold] {pause.wait_seconds:.0f}s (~{pause.wait_seconds/60:.1f} min)\n"
        f"[bold]Source:[/bold] {pause.source}\n"
    )
    if pause.resets_at is not None:
        detail += f"[bold]Resets at:[/bold] {pause.resets_at.isoformat()}\n"
    if pause.agent_name:
        detail += f"[bold]Agent:[/bold] {pause.agent_name}\n"
    detail += (
        "\nPipeline will automatically resume after the window resets.\n"
        "Press Ctrl+C to save state and exit."
    )
    console.print(Panel(
        detail,
        title="Rate Limit Reached — Pausing Pipeline",
        border_style="yellow",
    ))


async def _sleep_for_rate_limit_reset(
    wait_seconds: float,
    poll_interval: float = RATE_LIMIT_PAUSE_POLL_INTERVAL_SECONDS,
) -> bool:
    """Sleep in small increments, honouring shutdown signals.

    Returns ``True`` if the full wait elapsed; ``False`` if a shutdown
    signal (SIGINT/SIGTERM) was received mid-sleep.
    """
    from agentic_dev.orchestrator.shutdown import get_shutdown_event

    shutdown_event = get_shutdown_event()
    remaining = wait_seconds
    while remaining > 0:
        step = min(poll_interval, remaining)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=step)
            return False  # shutdown fired during the step
        except asyncio.TimeoutError:
            remaining -= step
    return True


async def _run_engine_with_rate_limit_resume(
    engine,
    event_log,
    *,
    max_consecutive_pauses: int = MAX_CONSECUTIVE_RATE_LIMIT_PAUSES,
    sleep_fn=_sleep_for_rate_limit_reset,
) -> None:
    """Run ``engine.run()`` in a loop, pausing and re-entering on RateLimitPause.

    Other exceptions (CheckpointPause, GracefulShutdown, AgenticDevError)
    are allowed to propagate to the caller unchanged.
    """
    from agentic_dev.logging import emit
    from agentic_dev.logging.events import PipelineRateLimitResumeEvent

    consecutive_pauses = 0
    while True:
        try:
            await engine.run()
            return
        except RateLimitPause as pause:
            consecutive_pauses += 1
            if consecutive_pauses > max_consecutive_pauses:
                console.print(
                    f"[bold red]Rate-limit pause threshold exceeded "
                    f"({max_consecutive_pauses} consecutive pauses). "
                    f"Aborting.[/bold red]"
                )
                raise
            _display_rate_limit_pause(pause)
            start = datetime.now(timezone.utc)
            completed = await sleep_fn(pause.wait_seconds)
            actual_wait_s = (datetime.now(timezone.utc) - start).total_seconds()
            if not completed:
                # Shutdown signalled during the pause — treat as graceful.
                raise GracefulShutdown(phase=pause.phase) from None
            emit(event_log, PipelineRateLimitResumeEvent(
                phase=pause.phase,
                actual_wait_seconds=actual_wait_s,
                message=(
                    f"Pipeline resuming at {pause.phase} "
                    f"after {actual_wait_s:.0f}s wait"
                ),
            ))


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

    from agentic_dev.config import load_project_config  # noqa: WPS433

    project_config = load_project_config(project_dir)
    checkpoint_config = project_config.checkpoint
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
        asyncio.run(_run_engine_with_rate_limit_resume(engine, _event_log))
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
        try:
            failed_phase = str(state_manager.load().phase)
        except Exception:
            failed_phase = str(state.phase)
        emit(_event_log, PipelineFailedEvent(
            error=str(exc),
            failed_at_phase=failed_phase,
            traceback=traceback.format_exc(),
            level="ERROR",
            message=f"Pipeline failed at {failed_phase}: {exc}",
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
    track: list[str] | None = typer.Option(
        None,
        "--track",
        help=(
            "Declare a codebase track. Format: name[::path[::kind[::uat_kind]]]. "
            "Repeatable; omit for single-track default."
        ),
    ),
) -> None:
    """Create a new project and start the development pipeline."""
    try:
        from agentic_dev.tracks import default_tracks, parse_track_spec  # noqa: WPS433

        declared_tracks = default_tracks()
        if track:
            try:
                declared_tracks = [parse_track_spec(s) for s in track]
            except ValueError as exc:
                console.print(f"[bold red]Invalid --track value: {exc}[/bold red]")
                raise typer.Exit(code=1)

        workspace_mgr = _get_workspace_manager(path)
        project_dir = workspace_mgr.create_project(app_name)
        console.print(f"[green]Created project workspace at {project_dir}[/green]")

        # Save initial pipeline state
        state_mgr = StateManager(project_dir)
        state = state_mgr.create_initial(app_name)
        state.tracks = declared_tracks
        state_mgr.save(state)

        from agentic_dev.config import register_project  # noqa: WPS433

        register_project(app_name, project_dir)

        # Save default checkpoint config
        _save_config(project_dir, CheckpointConfig())

        from agentic_dev.config import (  # noqa: WPS433
            load_project_config,
            save_project_config,
        )
        cfg = load_project_config(project_dir)
        cfg.tracks = declared_tracks
        names = ", ".join(t.name for t in declared_tracks)
        console.print(f"[cyan]Tracks: {names}[/cyan]")
        save_project_config(project_dir, cfg)

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

        doc_store = DocumentStore(project_dir)
        if user_input:
            doc_store.write("user_input", user_input)
            console.print("[green]Saved requirements to .agentic-dev/artifacts/user_input.md[/green]")

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

        if state.phase != PipelinePhase.COMPLETE:
            console.print(
                "[bold red]Project must be in COMPLETE state to update. "
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

        change_input = compose_remediation_input(
            uat_report, app_name, tracks=state.tracks
        )

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
