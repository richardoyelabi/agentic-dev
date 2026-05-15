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
console = Console()

app = typer.Typer(
    name="agentic-dev",
    help="Autonomous software development agency powered by Claude Code CLI.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _display_checkpoint(state: PipelineState, project_dir: Path) -> None:
    """Display a Rich panel when the pipeline pauses at a checkpoint."""
    from agentic_dev.onboarding.secrets import parse_secrets_template  # noqa: WPS433

    secrets_path = project_dir / AGENTIC_DEV_METADATA_DIR / "secrets.env"
    if state.phase == PipelinePhase.UAT:
        secrets_state = parse_secrets_template(secrets_path)
        if secrets_state.has_unfilled_required():
            unfilled = "\n".join(
                f"  - {key}" for key in secrets_state.unfilled_required
            )
            panel_content = (
                f"[bold]Project:[/bold] {state.project_name}\n"
                f"[bold]Phase:[/bold] {state.phase}\n\n"
                "[bold]Action required:[/bold] Fill the human-required secrets in\n"
                f"  [cyan].agentic-dev/secrets.env[/cyan]\n\n"
                f"[bold]Unfilled keys:[/bold]\n{unfilled}\n\n"
                "Then resume the pipeline:\n"
                "  [green]agentic-dev resume[/green]"
            )
            console.print(Panel(
                panel_content,
                title="UAT Paused — Secrets Required",
                border_style="yellow",
            ))
            return

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


def _resolve_managed_project() -> Path:
    """Return the agentic-dev project root containing ``cwd``.

    The cwd-based commands (``resume``, ``remediate``, ``status``, ``config``,
    ``logs``, ``cost``, ``tracks``) call this to find the project. When no
    ``.agentic-dev/`` is found anywhere in the cwd's ancestor chain, the
    command exits with a clear error pointing the user at ``agentic-dev work``.
    """
    from agentic_dev.config import resolve_project_dir  # noqa: WPS433

    project_dir = resolve_project_dir(Path.cwd())
    if not (project_dir / AGENTIC_DEV_METADATA_DIR).is_dir():
        console.print(
            "[bold red]No agentic-dev project found in the current directory "
            "or any parent. Run `agentic-dev work \"<prompt>\"` to start one."
            "[/bold red]"
        )
        raise typer.Exit(code=1)
    return project_dir


@app.command()
def resume(
    feedback: str | None = typer.Option(None, help="Feedback to inject into the next agent"),
    skip_sprint: int | None = typer.Option(
        None, "--skip-sprint", help="Skip the given sprint number (mark as complete)"
    ),
) -> None:
    """Resume a paused or failed pipeline in the current project."""
    try:
        project_dir = _resolve_managed_project()

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

        console.print(f"[green]Resuming project: {state.project_name}[/green]")
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
def remediate() -> None:
    """Fix UAT failures by running a full remediation pipeline cycle.

    Operates on the project containing ``cwd``. The most recent UAT report
    is read from the doc store and fed back to the pipeline as the change
    request, with ``mode=remediate`` so ``remediation_cycle`` is incremented.
    """
    try:
        project_dir = _resolve_managed_project()

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
            uat_report, state.project_name, tracks=state.tracks
        )

        console.print(
            f"[cyan]Starting remediation cycle {state.remediation_cycle + 1} "
            f"for {state.project_name}[/cyan]"
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
def status() -> None:
    """Show pipeline status: current phase, sprint progress, costs."""
    try:
        project_dir = _resolve_managed_project()
        state = StateManager(project_dir).load()
        _display_status(state)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def config(
    checkpoints: str | None = typer.Option(None, help="Comma-separated checkpoint names to enable"),
    autonomy: str | None = typer.Option(None, help="Autonomy level: full, default, or maximum"),
) -> None:
    """Configure checkpoint behaviour for the current project."""
    try:
        project_dir = _resolve_managed_project()

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
        console.print("[green]Configuration updated:[/green]")
        console.print(f"  after_design: {cfg.after_design}")
        console.print(f"  after_each_sprint: {cfg.after_each_sprint}")
        console.print(f"  before_uat: {cfg.before_uat}")

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


@app.command()
def logs(
    run: str | None = typer.Option(None, help="Specific run ID to view"),
    jsonl: bool = typer.Option(False, "--jsonl", help="Show JSON lines instead of human-readable log"),
    agent: str | None = typer.Option(None, help="Filter agent dumps by agent name"),
) -> None:
    """View pipeline run logs or agent dumps for the current project."""
    try:
        project_dir = _resolve_managed_project()

        logs_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
        if not logs_dir.exists():
            console.print("[yellow]No log files found.[/yellow]")
            return

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
def cost() -> None:
    """Show cost breakdown by agent and sprint for the current project."""
    try:
        project_dir = _resolve_managed_project()
        state = StateManager(project_dir).load()

        if not state.agent_runs:
            console.print("[yellow]No agent runs recorded yet.[/yellow]")
            return

        table = Table(title=f"Cost Breakdown: {state.project_name}")
        table.add_column("Agent", style="bold")
        table.add_column("Phase")
        table.add_column("Sprint", justify="right")
        table.add_column("Cost (USD)", justify="right")
        table.add_column("Status")

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
def tracks(
    rediscover: bool = typer.Option(
        False, "--rediscover", help="Re-run the discovery agent and overwrite the persisted tracks"
    ),
) -> None:
    """Show the project's inferred tracks; optionally re-run discovery."""
    try:
        project_dir = _resolve_managed_project()

        if rediscover:
            from agentic_dev.config import (  # noqa: WPS433
                load_project_config,
                save_project_config,
            )

            new_tracks = _resolve_tracks_for_in_place(project_dir, rediscover=True)
            cfg = load_project_config(project_dir)
            cfg.tracks = new_tracks
            save_project_config(project_dir, cfg)
            console.print(
                "[green]Persisted re-discovered tracks to "
                ".agentic-dev/config.json[/green]"
            )
            return

        from agentic_dev.config import load_project_config  # noqa: WPS433

        cfg = load_project_config(project_dir)
        table = Table(title="Tracks")
        table.add_column("Name", style="bold")
        table.add_column("Path")
        table.add_column("Kind")
        table.add_column("UAT kind")
        for track in cfg.tracks:
            table.add_row(
                track.name,
                track.path,
                track.kind,
                track.uat_kind or "-",
            )
        console.print(table)

    except AgenticDevError as exc:
        _display_error(exc)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# `work` — cwd-based command implementing the process-enforcer model.
# Subsequent invocations dispatch on pipeline state. First invocation runs
# track discovery, analyses any existing code, scaffolds ``.agentic-dev/``,
# and starts the deterministic pipeline.
# ---------------------------------------------------------------------------


def _collect_work_input(prompt: str | None, from_file: str | None) -> str:
    """Resolve the user's requirements text for a single ``work`` invocation."""
    if prompt and from_file:
        console.print(
            "[bold red]Cannot pass both a prompt and --from-file.[/bold red]"
        )
        raise typer.Exit(code=1)
    if from_file:
        return _read_requirements_file(from_file)
    if prompt:
        return prompt.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return _collect_user_requirements()


def _resolve_tracks_for_in_place(project_dir: Path, rediscover: bool) -> list:
    """Return tracks for an in-place project: ``agentic-dev.yaml`` > discovery."""
    from agentic_dev.discovery import discover_tracks, load_track_override  # noqa: WPS433

    if not rediscover:
        override = load_track_override(project_dir)
        if override is not None:
            console.print(
                f"[cyan]Loaded {len(override)} track(s) from agentic-dev.yaml[/cyan]"
            )
            return override

    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433

    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    console.print("[cyan]Discovering project structure...[/cyan]")
    claude = ClaudeRunner(log_dir=log_dir)
    discovery = asyncio.run(discover_tracks(claude, project_dir))
    summary = ", ".join(f"{t.name} ({t.kind})" for t in discovery.tracks)
    console.print(f"  [green]Detected:[/green] {summary}")
    if discovery.reasoning:
        console.print(f"  [dim]{discovery.reasoning}[/dim]")
    return discovery.tracks


_NON_CODE_ENTRIES = frozenset({
    AGENTIC_DEV_METADATA_DIR,
    "agentic-dev.yaml",
    ".git",
    ".gitignore",
})


def _track_has_existing_code(project_dir: Path, track) -> bool:
    """Return True iff the track's directory has source files worth analysing.

    Pure-metadata entries (``.agentic-dev/``, the override YAML, git
    bookkeeping) don't count as "existing code" — the analyser would have
    nothing useful to report on them.
    """
    track_dir = project_dir / track.path
    if not track_dir.is_dir():
        return False
    return any(
        entry.name not in _NON_CODE_ENTRIES for entry in track_dir.iterdir()
    )


def _analyze_existing_tracks(project_dir: Path, tracks: list, doc_store: DocumentStore) -> None:
    """Run the codebase analyser on each non-empty track in parallel."""
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.onboarding.analyzer import analyze_codebases  # noqa: WPS433
    from agentic_dev.onboarding.models import AnnotatedSource  # noqa: WPS433

    sources: list[AnnotatedSource] = []
    sources_meta: list = []
    for track in tracks:
        if not _track_has_existing_code(project_dir, track):
            continue
        track_path = project_dir / track.path
        sources.append(
            AnnotatedSource(
                value=str(track_path),
                annotation=f"{track.name} ({track.kind})",
            )
        )
        sources_meta.append(track)

    if not sources:
        console.print("[dim]No existing code detected — skipping analysis pass.[/dim]")
        return

    console.print(
        f"[cyan]Analysing existing code in {len(sources)} track(s) in parallel...[/cyan]"
    )
    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    claude = ClaudeRunner(log_dir=log_dir)
    results = asyncio.run(analyze_codebases(claude, sources))

    combined: list[str] = []
    for track, result in zip(sources_meta, results):
        doc_store.write(f"track_{track.name}_analysis", result.text)
        combined.append(f"## {track.name} ({track.kind})\n\n{result.text}")
    doc_store.write("existing_code_analyses", "\n\n---\n\n".join(combined))


def _detect_project_environment(
    project_dir: Path, tracks: list, doc_store: DocumentStore
) -> None:
    """Run the environment detector and persist bootstrap.md + secrets template."""
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.onboarding.environment import (  # noqa: WPS433
        detect_environment,
    )

    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    claude = ClaudeRunner(log_dir=log_dir)

    console.print("[cyan]Detecting bootstrap commands and env requirements...[/cyan]")
    report = asyncio.run(detect_environment(claude, project_dir, tracks))

    doc_store.write("bootstrap", report.bootstrap_md)
    doc_store.write("env_requirements", report.env_requirements_md)

    secrets_path = project_dir / AGENTIC_DEV_METADATA_DIR / "secrets.env"
    if not secrets_path.exists():
        secrets_path.write_text(report.secrets_env_template, encoding="utf-8")


def _extract_and_persist_figma_annotations(
    figma_sources: list,
    project_dir: Path,
    doc_store: DocumentStore,
) -> None:
    """Run the annotation extractor and persist its output.

    Best-effort: failures are logged as warnings and the pipeline continues.
    Annotations are advisory — missing them must never block onboarding or
    an update cycle.
    """
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.onboarding.figma_annotations import (  # noqa: WPS433
        extract_figma_annotations,
        write_figma_annotations,
    )

    console.print("[cyan]Extracting Figma annotations...[/cyan]")
    log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(
            extract_figma_annotations(
                ClaudeRunner(log_dir=log_dir),
                figma_sources,
                project_dir,
            )
        )
    except Exception as exc:  # noqa: BLE001 — annotations are advisory
        console.print(
            f"[yellow]Could not extract Figma annotations ({exc}). "
            "Continuing without them.[/yellow]"
        )
        return

    write_figma_annotations(doc_store, result.text)
    if doc_store.exists("figma_annotations"):
        console.print(
            "[green]Saved Figma annotations to "
            ".agentic-dev/artifacts/figma_annotations.md[/green]"
        )


def _persist_figma_inputs(
    figma_sources: list,
    project_dir: Path,
    doc_store: DocumentStore,
) -> None:
    """Validate Figma MCP, persist sources, and run the annotation extractor.

    Raises ``FigmaMCPNotConfigured`` (an ``AgenticDevError``) when no Figma
    MCP server is configured — the caller is expected to surface this as a
    user-facing exit.
    """
    from agentic_dev.onboarding.figma import (  # noqa: WPS433
        check_figma_mcp_available,
        write_figma_sources,
    )

    if not figma_sources:
        return

    check_figma_mcp_available()
    write_figma_sources(doc_store, figma_sources)
    _extract_and_persist_figma_annotations(figma_sources, project_dir, doc_store)


def _update_figma_inputs(
    figma_sources: list,
    project_dir: Path,
    doc_store: DocumentStore,
    tracks: list,
) -> str | None:
    """Update-cycle Figma handling: validate, persist, detect changes, refresh.

    Mirrors :func:`_persist_figma_inputs` but additionally diffs the new
    Figma state against the existing per-track specs and the previously
    extracted annotations. Returns the design-change summary (or ``None``
    when nothing changed) for the caller to thread into the update cycle.

    Raises ``FigmaMCPNotConfigured`` when no Figma MCP server is configured.
    """
    from agentic_dev.claude.runner import ClaudeRunner  # noqa: WPS433
    from agentic_dev.onboarding.figma import (  # noqa: WPS433
        check_figma_mcp_available,
        detect_design_changes,
        write_figma_sources,
    )

    if not figma_sources:
        return None

    check_figma_mcp_available()
    write_figma_sources(doc_store, figma_sources)

    existing_annotations = (
        doc_store.read("figma_annotations")
        if doc_store.exists("figma_annotations")
        else ""
    )

    ui_spec_chunks: list[str] = []
    for track in tracks:
        if track.kind not in ("web", "desktop", "mobile"):
            continue
        spec_name = f"{track.name}_spec"
        if doc_store.exists(spec_name):
            ui_spec_chunks.append(
                f"## {track.name} ({track.kind})\n\n{doc_store.read(spec_name)}"
            )

    design_changes: str | None = None
    if ui_spec_chunks:
        log_dir = project_dir / AGENTIC_DEV_METADATA_DIR / LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        figma_claude = ClaudeRunner(log_dir=log_dir)
        console.print(
            "[cyan]Detecting design changes against existing specs...[/cyan]"
        )
        change_result = asyncio.run(
            detect_design_changes(
                figma_claude,
                figma_sources,
                "\n\n".join(ui_spec_chunks),
                project_dir,
                existing_annotations=existing_annotations,
            )
        )
        if change_result.has_changes:
            design_changes = change_result.summary
        else:
            console.print("[green]No design changes detected.[/green]")

    _extract_and_persist_figma_annotations(figma_sources, project_dir, doc_store)

    return design_changes


def _onboard_in_place(
    project_dir: Path,
    user_input: str,
    rediscover: bool = False,
    figma_sources: list | None = None,
) -> PipelineState:
    """First-run onboarding: discover tracks, scaffold ``.agentic-dev/``, persist state."""
    from agentic_dev.config import (  # noqa: WPS433
        ProjectConfig,
        save_project_config,
    )
    from agentic_dev.workspace.manager import ensure_scaffold  # noqa: WPS433

    tracks = _resolve_tracks_for_in_place(project_dir, rediscover)

    ensure_scaffold(project_dir)

    cfg = ProjectConfig(app_name=project_dir.name, tracks=tracks)
    save_project_config(project_dir, cfg)

    state_mgr = StateManager(project_dir)
    state = state_mgr.create_initial(project_dir.name)
    state.tracks = tracks
    state_mgr.save(state)

    doc_store = DocumentStore(project_dir)
    _analyze_existing_tracks(project_dir, tracks, doc_store)
    _detect_project_environment(project_dir, tracks, doc_store)
    if user_input:
        doc_store.write("user_input", user_input)

    _persist_figma_inputs(figma_sources or [], project_dir, doc_store)

    return state


@app.command()
def work(
    prompt: str | None = typer.Argument(
        None,
        help="What you'd like agentic-dev to do. Omit to provide via stdin or --from-file.",
    ),
    from_file: str | None = typer.Option(
        None, "--from-file", help="Read the work request from a file"
    ),
    from_figma: list[str] | None = typer.Option(
        None,
        help="Figma URL with optional '::annotation' (repeatable)",
    ),
    rediscover: bool = typer.Option(
        False,
        "--rediscover",
        help="Re-run track discovery even if a config already exists",
    ),
) -> None:
    """Run agentic-dev against the project containing the current directory.

    First invocation: walks up from ``cwd`` for ``.agentic-dev/``, scaffolds
    one in the cwd if absent, discovers tracks (or loads
    ``agentic-dev.yaml``), analyses any existing code in each track, and
    starts the deterministic pipeline.

    Subsequent invocations dispatch on pipeline state:

    - ``COMPLETE`` → the prompt is enqueued as an update cycle.
    - ``FAILED`` → the prompt is injected as feedback and the pipeline
      resumes from the failed phase.
    - mid-pipeline → exits with an error pointing at ``resume``.
    """
    from agentic_dev.config import resolve_project_dir  # noqa: WPS433

    try:
        from agentic_dev.onboarding.models import AnnotatedSource  # noqa: WPS433

        project_dir = resolve_project_dir(Path.cwd())
        change_input = _collect_work_input(prompt, from_file)

        figma_sources = [AnnotatedSource.parse(s) for s in (from_figma or [])]

        is_first_run = not (project_dir / AGENTIC_DEV_METADATA_DIR).is_dir()

        if is_first_run:
            if not change_input and not figma_sources:
                console.print(
                    "[bold red]No requirements provided. Pass a prompt, "
                    "--from-file, or --from-figma.[/bold red]"
                )
                raise typer.Exit(code=1)
            console.print(f"[green]Working on project at {project_dir}[/green]")
            state = _onboard_in_place(
                project_dir,
                change_input,
                rediscover=rediscover,
                figma_sources=figma_sources,
            )
            _run_pipeline(project_dir, state)
            return

        state_mgr = StateManager(project_dir)
        state = state_mgr.load()

        if state.phase == PipelinePhase.COMPLETE:
            if not change_input and not figma_sources:
                console.print(
                    "[bold red]No change description provided. Pass a prompt, "
                    "--from-file, or --from-figma.[/bold red]"
                )
                raise typer.Exit(code=1)
            console.print(
                f"[cyan]Project at {project_dir} is COMPLETE — "
                "enqueuing the new prompt as an update.[/cyan]"
            )
            design_changes = _update_figma_inputs(
                figma_sources,
                project_dir,
                DocumentStore(project_dir),
                state.tracks,
            )
            _start_update_cycle(
                project_dir=project_dir,
                state=state,
                state_mgr=state_mgr,
                change_input=change_input or None,
                mode="update",
                restart_phase=PipelinePhase.FEATURE_ANALYSIS,
                is_targeted=True,
                design_changes=design_changes,
            )
            return

        if state.phase == PipelinePhase.FAILED:
            from agentic_dev.state.transitions import resume_from_failure  # noqa: WPS433

            console.print(
                "[yellow]Pipeline previously FAILED — auto-resuming "
                "with the new prompt as feedback.[/yellow]"
            )
            if change_input:
                state.checkpoint_feedback = change_input
            state = resume_from_failure(state)
            state_mgr.save(state)
            _run_pipeline(project_dir, state)
            return

        console.print(
            f"[bold red]Pipeline already in progress (phase={state.phase}). "
            "Use `agentic-dev resume` to continue it before starting new work."
            "[/bold red]"
        )
        raise typer.Exit(code=1)

    except (AgenticDevError, RuntimeError) as exc:
        _display_error(exc)
        raise typer.Exit(code=1)
