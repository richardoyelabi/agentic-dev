"""Pipeline engine: main coordinator that ties all phases together."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.config import RATE_LIMIT_PAUSE_MAX_SECONDS
from agentic_dev.claude.llm_parser import parse_with_llm
from agentic_dev.claude.output_parser import OutputParser
from agentic_dev.claude.rate_limiter import UsageApiClient, WaitStrategy
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import (
    AgentRunError,
    CheckpointPause,
    GracefulShutdown,
    OutputParseError,
    RateLimitError,
    RateLimitPause,
)
from agentic_dev.orchestrator.agent_bridge import to_run_config
from agentic_dev.orchestrator.checkpoint import CheckpointConfig, should_pause
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle
from agentic_dev.orchestrator.shutdown import get_shutdown_event, install_signal_handlers
from agentic_dev.orchestrator.sprint_runner import SprintRunner
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import (
    AgentRunRecord,
    PipelinePhase,
    PipelineState,
    SprintState,
    SprintStatus,
)
from agentic_dev.state.parser_models import ParsedSprintPlan
from agentic_dev.state.transitions import advance_phase
from agentic_dev.tracks import Track, default_tracks
from agentic_dev.workspace.claude_md import (
    generate_track_claude_md,
    parse_tech_stack,
    write_claude_md,
)
from agentic_dev.workspace.git import (
    commit,
    get_committed_content,
    has_changes,
    init_repo,
)

logger = logging.getLogger(__name__)


class PipelineEngine:
    """Finite state machine that advances through pipeline phases."""

    def __init__(
        self,
        project_dir: Path,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        state_manager: StateManager,
        checkpoint_config: CheckpointConfig,
    ) -> None:
        self._project_dir = project_dir
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._state_manager = state_manager
        self._checkpoint_config = checkpoint_config
        self._output_parser = OutputParser()

    def _resolve_tracks(self, state: PipelineState | None) -> list[Track]:
        """Return the project's declared tracks, defaulting to a single ``app`` track."""
        if state is not None and state.tracks:
            return state.tracks
        return default_tracks()

    def _get_sprint_runner(
        self, state: PipelineState | None = None,
    ) -> SprintRunner:
        """Create a SprintRunner for this project's tracks."""
        return SprintRunner(
            claude=self._claude,
            registry=self._registry,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            project_dir=self._project_dir,
            tracks=self._resolve_tracks(state),
            state_manager=self._state_manager,
            pipeline_state=state,
        )

    async def _compute_rate_limit_wait(
        self,
    ) -> tuple[float, datetime | None, str]:
        """Determine how long to pause before re-entering the pipeline.

        Prefers the Anthropic usage API for an authoritative reset time;
        falls back to a short default when the API is unavailable.

        Returns ``(wait_seconds, resets_at, source)`` where *source* is one
        of ``"usage_api"`` or ``"fallback"``.
        """
        try:
            client = UsageApiClient()
            status = await client.get_utilization()
        except Exception:  # noqa: BLE001 — degrade gracefully
            status = None

        if status and status.resets_at:
            delta = (status.resets_at - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return delta + WaitStrategy.BUFFER, status.resets_at, "usage_api"

        # Fallback: 5 minutes is long enough to clear transient bursts and
        # short enough that a misdetection doesn't tie up the terminal.
        return 300.0, None, "fallback"

    def _emit_rate_limit_pause(
        self,
        *,
        phase: PipelinePhase,
        wait_seconds: float,
        resets_at: datetime | None,
        source: str,
        agent_name: str | None,
    ) -> None:
        """Emit the structured log event recording a pipeline-level rate-limit pause."""
        from agentic_dev.logging import emit, get_event_logger
        from agentic_dev.logging.events import PipelineRateLimitPauseEvent

        _event_log = get_event_logger("engine")
        resets_iso = resets_at.isoformat() if resets_at else None
        emit(_event_log, PipelineRateLimitPauseEvent(
            phase=str(phase),
            wait_seconds=wait_seconds,
            resets_at=resets_iso,
            source=source,
            agent_name=agent_name,
            level="WARNING",
            message=(
                f"Pipeline pausing at {phase} for {wait_seconds:.0f}s "
                f"(source={source}"
                + (f", resets_at={resets_iso}" if resets_iso else "")
                + ")"
            ),
        ))

    async def run(self) -> None:
        """Main loop: load state, execute current phase, advance, persist.

        Raises CheckpointPause when the pipeline should pause for human review.
        Raises GracefulShutdown when a SIGINT/SIGTERM signal is received.
        """
        install_signal_handlers()
        state = self._state_manager.load()
        shutdown_event = get_shutdown_event()

        while state.phase not in (
            PipelinePhase.COMPLETE, PipelinePhase.FAILED,
        ):
            if shutdown_event.is_set():
                self._state_manager.save(state)
                raise GracefulShutdown(phase=state.phase)

            try:
                state = await self._execute_phase(state)
            except RateLimitError as exc:
                # Pause-and-resume path: preserve phase, let CLI sleep,
                # re-enter engine.run(). Do NOT transition to FAILED.
                wait_seconds, resets_at, source = await self._compute_rate_limit_wait()
                if wait_seconds > RATE_LIMIT_PAUSE_MAX_SECONDS:
                    # Reset is too far away — fail rather than block forever.
                    state.failed_at_phase = state.phase
                    state.phase = PipelinePhase.FAILED
                    state.error = (
                        f"Rate-limit reset exceeds pause cap "
                        f"({wait_seconds:.0f}s > {RATE_LIMIT_PAUSE_MAX_SECONDS}s): {exc}"
                    )
                    self._state_manager.save(state)
                    raise
                self._state_manager.save(state)  # phase preserved
                self._emit_rate_limit_pause(
                    phase=state.phase,
                    wait_seconds=wait_seconds,
                    resets_at=resets_at,
                    source=source,
                    agent_name=exc.agent_name,
                )
                raise RateLimitPause(
                    phase=str(state.phase),
                    wait_seconds=wait_seconds,
                    resets_at=resets_at,
                    source=source,
                    agent_name=exc.agent_name,
                )
            except (AgentRunError, OutputParseError) as exc:
                state.failed_at_phase = state.phase
                state.phase = PipelinePhase.FAILED
                state.error = str(exc)
                self._state_manager.save(state)
                raise

            self._state_manager.save(state)

            if should_pause(state.phase, self._checkpoint_config):
                raise CheckpointPause(phase=state.phase)

    async def _execute_phase(self, state: PipelineState) -> PipelineState:
        """Dispatch to the appropriate handler based on the current phase."""
        handlers = {
            PipelinePhase.IDLE: self._run_input_processing,
            PipelinePhase.INPUT_PROCESSING: self._run_input_processing,
            PipelinePhase.FEATURE_ANALYSIS: self._run_feature_analysis,
            PipelinePhase.ARCHITECTURE: self._run_architecture,
            PipelinePhase.SPRINT_PLANNING: self._run_sprint_planning,
            PipelinePhase.DESIGN_CHECKPOINT: self._advance_past_checkpoint,
            PipelinePhase.SPRINTING: self._run_sprints,
            PipelinePhase.UAT: self._run_uat,
        }

        # QA sub-phases advance to the next main phase
        qa_advance_map = {
            PipelinePhase.INPUT_PROCESSING_QA: PipelinePhase.FEATURE_ANALYSIS,
            PipelinePhase.FEATURE_ANALYSIS_QA: PipelinePhase.ARCHITECTURE,
            PipelinePhase.ARCHITECTURE_QA: PipelinePhase.SPRINT_PLANNING,
            PipelinePhase.SPRINT_PLANNING_QA: PipelinePhase.DESIGN_CHECKPOINT,
            PipelinePhase.UAT_QA: PipelinePhase.COMPLETE,
        }

        if state.phase in qa_advance_map:
            return advance_phase(state, qa_advance_map[state.phase])

        handler = handlers.get(state.phase)
        if handler is None:
            return state

        return await handler(state)

    async def _run_input_processing(self, state: PipelineState) -> PipelineState:
        """Run the input processor agent with QA cycle."""
        if state.phase == PipelinePhase.IDLE:
            state = advance_phase(state, PipelinePhase.INPUT_PROCESSING)

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("input_processor"),
            qa_agent=self._registry.get("input_processor_qa"),
            input_docs={"user_input": self._doc_store.read("user_input")},
            output_doc_name="structured_input",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            session_id=state.active_session_id,
        )

        state.total_cost_usd += result.total_cost
        state.active_session_id = None
        self._record_agent_run(state, "input_processor", result.total_cost)
        await self._commit_docs_changes("docs: structured input from requirements")

        # Materialise each declared track's working directory.
        for track in self._resolve_tracks(state):
            (self._project_dir / track.path).mkdir(parents=True, exist_ok=True)

        return advance_phase(state, PipelinePhase.INPUT_PROCESSING_QA)

    async def _merge_change_request(self, state: PipelineState) -> None:
        """Merge a pending change_request into structured_input.

        Runs the ``input_updater`` agent with QA cycle which surgically applies
        the change request to the existing structured input.  The change_request
        document is deleted afterwards so the merge is not repeated on pipeline
        resume.
        """
        structured_input = self._doc_store.read("structured_input")
        change_request = self._doc_store.read("change_request")

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("input_updater"),
            qa_agent=self._registry.get("input_updater_qa"),
            input_docs={
                "structured_input": structured_input,
                "change_request": change_request,
            },
            output_doc_name="structured_input",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            qa_output_key="merged_output",
        )

        state.total_cost_usd += result.total_cost
        self._record_agent_run(state, "input_updater", result.total_cost)
        self._doc_store.delete("change_request")
        await self._commit_docs_changes("docs: updated structured input from change request")

    def _update_extra_context(self, state: PipelineState) -> dict[str, str]:
        """Build extra_context dict with change context when in update mode."""
        extra_context: dict[str, str] = {}
        if state.mode == "update" and self._doc_store.exists("user_input"):
            extra_context["change_request"] = self._doc_store.read("user_input")
        if self._doc_store.exists("spec_changes"):
            extra_context["spec_changes"] = self._doc_store.read("spec_changes")
        if self._doc_store.exists("design_changes"):
            extra_context["design_changes"] = self._doc_store.read("design_changes")
        return extra_context

    async def _run_feature_analysis(self, state: PipelineState) -> PipelineState:
        """Run feature_analyst + feature_analyst_qa via QA cycle."""
        if self._doc_store.exists("change_request"):
            await self._merge_change_request(state)

        structured_input = self._doc_store.read("structured_input")
        extra_context = self._update_extra_context(state)

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("feature_analyst"),
            qa_agent=self._registry.get("feature_analyst_qa"),
            input_docs={"structured_input": structured_input},
            output_doc_name="features",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            session_id=state.active_session_id,
            extra_context=extra_context,
        )

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        state.active_session_id = None
        self._record_agent_run(state, "feature_analyst", total_cost)
        await self._commit_docs_changes("docs: feature analysis")

        return advance_phase(state, PipelinePhase.FEATURE_ANALYSIS_QA)

    async def _run_architecture(self, state: PipelineState) -> PipelineState:
        """Run architect + architect_qa. Parse multi-document output (per-track)."""
        from agentic_dev.tracks import expected_architecture_docs  # noqa: WPS433

        features = self._doc_store.read("features")
        structured_input = self._doc_store.read("structured_input")
        figma_sources = (
            self._doc_store.read("figma_sources")
            if self._doc_store.exists("figma_sources")
            else ""
        )
        figma_mcp_available = "false"
        if figma_sources:
            try:
                from agentic_dev.onboarding.figma import check_figma_mcp_available  # noqa: WPS433

                check_figma_mcp_available()
                figma_mcp_available = "true"
            except Exception:  # noqa: BLE001
                pass

        existing_code_analyses = (
            self._doc_store.read("existing_code_analyses")
            if self._doc_store.exists("existing_code_analyses")
            else ""
        )

        tracks = self._resolve_tracks(state)
        extra_context: dict[str, object] = {"tracks": tracks}
        extra_context.update(self._update_extra_context(state))
        if existing_code_analyses:
            extra_context["existing_code_analyses"] = existing_code_analyses

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("architect"),
            qa_agent=self._registry.get("architect_qa"),
            input_docs={
                "structured_input": structured_input,
                "features": features,
                "figma_sources": figma_sources,
                "figma_mcp_available": figma_mcp_available,
            },
            output_doc_name="architecture",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            extra_context=extra_context,
            session_id=state.active_session_id,
        )

        # Split multi-document output into separate per-track specs
        expected = expected_architecture_docs(tracks)
        docs = self._output_parser.split_documents(
            result.output,
            expected_documents=expected,
            agent_name="architect",
        )
        for doc_name, content in docs.items():
            self._doc_store.write(doc_name, content)

        # During updates, restore archived specs that didn't materially change
        # to prevent phantom drift from regeneration
        if state.mode == "update":
            await self._restore_unchanged_specs(docs)

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        state.active_session_id = None
        self._record_agent_run(state, "architect", total_cost)
        await self._commit_docs_changes("docs: architecture specs")

        return advance_phase(state, PipelinePhase.ARCHITECTURE_QA)

    async def _run_sprint_planning(self, state: PipelineState) -> PipelineState:
        """Run sprint_planner + sprint_planner_qa. Parse sprint plan."""
        features = self._doc_store.read("features")
        input_docs: dict[str, str] = {"features": features}

        tracks = self._resolve_tracks(state)
        track_specs: dict[str, str] = {}
        for track in tracks:
            spec_name = f"{track.name}_spec"
            if self._doc_store.exists(spec_name):
                track_specs[spec_name] = self._doc_store.read(spec_name)
        if self._doc_store.exists("api_contract"):
            input_docs["api_contract"] = self._doc_store.read("api_contract")
        else:
            input_docs["api_contract"] = ""

        extra_context: dict[str, object] = {
            "tracks": tracks,
            "track_specs": track_specs,
        }
        extra_context.update(self._update_extra_context(state))

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("sprint_planner"),
            qa_agent=self._registry.get("sprint_planner_qa"),
            input_docs=input_docs,
            output_doc_name="sprint_plan",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            session_id=state.active_session_id,
            extra_context=extra_context,
        )

        # Populate sprint states from the plan output
        sprint_docs = self._output_parser.split_documents(
            result.output,
            expected_documents=["sprint_plan"],
            agent_name="sprint_planner",
        )
        sprint_plan_text = sprint_docs.get("sprint_plan", result.output)
        state.sprints = await self._parse_sprint_plan(sprint_plan_text)
        state.current_sprint = 1 if state.sprints else None

        # Validate feature conventions in sprint scopes
        convention_warnings = self._validate_sprint_feature_conventions(
            state.sprints, sprint_plan_text,
        )
        if convention_warnings:
            from agentic_dev.logging import get_event_logger
            _log = get_event_logger("engine")
            for w in convention_warnings:
                _log.warning(w)

        # Write sprint scope documents so developers receive full sprint context
        for sprint in state.sprints:
            if sprint.scope_text:
                self._doc_store.write(
                    f"sprint_{sprint.sprint_number}_scope",
                    sprint.scope_text,
                )

        for sprint in state.sprints:
            if sprint.integration_services:
                self._doc_store.write(
                    f"sprint_{sprint.sprint_number}_integration_flag",
                    ", ".join(sprint.integration_services),
                )

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        state.active_session_id = None
        self._record_agent_run(state, "sprint_planner", total_cost)
        await self._commit_docs_changes("docs: sprint plan")

        return advance_phase(state, PipelinePhase.SPRINT_PLANNING_QA)

    async def _advance_past_checkpoint(self, state: PipelineState) -> PipelineState:
        """Advance past a checkpoint to the sprinting phase.

        If checkpoint_feedback was provided, store it so sprint agents can
        reference it as additional context.
        """
        if state.checkpoint_feedback:
            self._doc_store.write("checkpoint_feedback", state.checkpoint_feedback)
            state.checkpoint_feedback = None

        await self._commit_docs_changes("docs: checkpoint feedback")
        await self._setup_workspaces(state)

        return advance_phase(state, PipelinePhase.SPRINTING)

    async def _setup_workspaces(self, state: PipelineState) -> None:
        """Initialize git repos and generate CLAUDE.md for each track's directory.

        Skipped for update and remediate modes where the workspace already
        exists with its own CLAUDE.md, git history, and committed code.
        """
        if state.mode in ("update", "remediate"):
            return

        project_name = state.project_name
        for track in self._resolve_tracks(state):
            track_dir = self._project_dir / track.path
            track_dir.mkdir(parents=True, exist_ok=True)
            tech_stack = self._read_tech_stack(f"{track.name}_spec")
            content = generate_track_claude_md(project_name, track, tech_stack)
            write_claude_md(track_dir, content)
            await init_repo(track_dir)
            await commit(track_dir, "Initial commit: project scaffold and CLAUDE.md")

    async def _commit_sprint_changes(
        self, state: PipelineState, sprint: SprintState
    ) -> None:
        """Commit changes in each track's directory after a successful sprint."""
        message = f"Sprint {sprint.sprint_number}: {sprint.name}"
        for track in self._resolve_tracks(state):
            track_dir = self._project_dir / track.path
            if track_dir.is_dir() and await has_changes(track_dir):
                await commit(track_dir, message)

    async def _commit_docs_changes(self, message: str) -> None:
        """Commit changes in the docs directory if there are any.

        Initializes the docs git repo on-the-fly for backward compatibility
        with existing projects created before docs versioning was added.
        """
        docs_dir = self._doc_store.docs_dir
        if not docs_dir.is_dir():
            return
        if not (docs_dir / ".git").is_dir():
            await init_repo(docs_dir)
        if await has_changes(docs_dir):
            try:
                await commit(docs_dir, message)
            except RuntimeError:
                logger.warning("docs commit failed for '%s'; continuing", message)

    async def _restore_unchanged_specs(self, new_docs: dict[str, str]) -> None:
        """Restore committed specs that didn't materially change.

        After architecture regeneration during updates, compare each new spec
        against the version committed in git HEAD. If the content is
        substantively the same (ignoring whitespace), restore the committed
        version to prevent phantom drift.
        """
        docs_dir = self._doc_store.docs_dir
        for doc_name, new_content in new_docs.items():
            old_content = await get_committed_content(docs_dir, f"{doc_name}.md")
            if old_content is None:
                continue
            if old_content.strip() == new_content.strip():
                self._doc_store.write(doc_name, old_content)

    def _read_tech_stack(self, doc_name: str) -> dict[str, str]:
        """Read a spec document and parse its tech stack, returning defaults on failure."""
        try:
            spec_text = self._doc_store.read(doc_name)
            return parse_tech_stack(spec_text)
        except Exception:
            return {}

    def _validate_sprint_mcp_services(
        self, sprints: list[SprintState],
    ) -> list[str]:
        """Check MCP readiness for all integration services across sprints.

        Returns a list of human-readable warning strings for services
        that are not configured in Claude Code settings. Does not block execution.
        """
        from agentic_dev.mcp.claude_settings import (
            discover_mcp_servers,
            find_server_for_service,
        )

        all_services: set[str] = set()
        for sprint in sprints:
            all_services.update(sprint.integration_services)

        if not all_services:
            return []

        env = discover_mcp_servers(project_dir=self._project_dir)
        warnings: list[str] = []
        for service in sorted(all_services):
            if find_server_for_service(env, service) is None:
                warnings.append(
                    f"No MCP server for '{service}' found in Claude Code "
                    f"settings. Run 'claude mcp add {service}' to configure it."
                )
        return warnings

    async def _run_sprints(self, state: PipelineState) -> PipelineState:
        """Run each sprint sequentially using SprintRunner."""
        sprint_runner = self._get_sprint_runner(state=state)

        mcp_warnings = self._validate_sprint_mcp_services(state.sprints)
        if mcp_warnings:
            from agentic_dev.logging import get_event_logger, emit
            from agentic_dev.logging.events import MCPValidationEvent
            _event_log = get_event_logger("engine")
            emit(_event_log, MCPValidationEvent(
                warnings=mcp_warnings,
                message="MCP validation: " + "; ".join(mcp_warnings),
            ))

        for sprint in state.sprints:
            if sprint.status == SprintStatus.COMPLETE:
                continue

            if sprint.status == SprintStatus.PENDING:
                sprint.status = SprintStatus.IN_PROGRESS
            sprint.started_at = sprint.started_at or datetime.now(timezone.utc)
            state.current_sprint = sprint.sprint_number

            sprint_scope = self._doc_store.read(
                f"sprint_{sprint.sprint_number}_scope"
            ) if self._doc_store.exists(
                f"sprint_{sprint.sprint_number}_scope"
            ) else sprint.name

            needs_integration = self._doc_store.exists(
                f"sprint_{sprint.sprint_number}_integration_flag"
            )

            result = await sprint_runner.run_sprint(
                sprint_number=sprint.sprint_number,
                sprint_scope=sprint_scope,
                sprint_state=sprint,
                needs_integration=needs_integration,
            )

            sprint.completed_at = datetime.now(timezone.utc)
            state.total_cost_usd += result.total_cost

            if result.success:
                sprint.status = SprintStatus.COMPLETE
                await self._commit_sprint_changes(state, sprint)
                await self._commit_docs_changes(
                    f"docs: sprint {sprint.sprint_number} reports"
                )
            else:
                sprint.failed_at_step = sprint.status
                sprint.status = SprintStatus.FAILED

            self._state_manager.save(state)

            if should_pause(
                PipelinePhase.SPRINTING,
                self._checkpoint_config,
                sprint_just_completed=True,
            ):
                raise CheckpointPause(phase=PipelinePhase.SPRINTING)

            if not result.success:
                state.failed_at_phase = PipelinePhase.SPRINTING
                state.error = f"Sprint {sprint.sprint_number} failed"
                return advance_phase(state, PipelinePhase.FAILED)

        return advance_phase(state, PipelinePhase.UAT)

    async def _run_uat(self, state: PipelineState) -> PipelineState:
        """Run UAT for every UAT-capable track and aggregate the verdicts."""
        from agentic_dev.config import load_project_config
        from agentic_dev.uat.aggregator import aggregate_uat_reports
        from agentic_dev.uat.dispatcher import (
            _read_desktop_framework,
            pick_uat_agent,
        )
        from agentic_dev.uat.preinstall import preinstall_for_uat
        from agentic_dev.uat.prereqs import (
            check_prereqs,
            render_doc as render_prereqs_doc,
        )
        from agentic_dev.uat.secrets_gate import check_secrets_gate
        from agentic_dev.uat.validator import validate_uat_report

        tracks = self._resolve_tracks(state)
        uat_tracks = [t for t in tracks if t.uat_kind]
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if not uat_tracks:
            self._doc_store.write(
                "uat_report",
                "## Overall Result: FAIL\n\nNo UAT-capable tracks declared.\n",
            )
            await self._commit_docs_changes("docs: UAT report (no UAT tracks)")
            return advance_phase(state, PipelinePhase.UAT_QA)

        check_secrets_gate(self._project_dir, self._doc_store)
        preinstall_for_uat(
            project_dir=self._project_dir,
            run_id=run_id,
            tracks=uat_tracks,
            doc_store=self._doc_store,
        )

        cfg = load_project_config(self._project_dir)
        per_track_reports: dict[str, str] = {}
        total_cost = 0.0

        for track in uat_tracks:
            desktop_framework: str | None = None
            spec_doc = f"{track.name}_spec"
            if (
                track.uat_kind == "desktop"
                and self._doc_store.exists(spec_doc)
            ):
                desktop_framework = _read_desktop_framework(
                    self._doc_store.read(spec_doc)
                )

            prereq_result = check_prereqs(
                track=track,
                desktop_framework=desktop_framework,
                project_dir=self._project_dir,
            )
            self._doc_store.write(
                f"uat_prereqs_{track.name}",
                render_prereqs_doc(prereq_result),
            )

            agent_name = pick_uat_agent(track, desktop_framework)
            agent_def = self._registry.get(agent_name)

            input_docs: dict[str, str] = {}
            for doc_name in agent_def.input_documents:
                if self._doc_store.exists(doc_name):
                    input_docs[doc_name] = self._doc_store.read(doc_name)
            # Back-compat alias: older specs used "features"; the new UAT agents
            # declare "features_request". Supply it from either name.
            if (
                "features_request" in agent_def.input_documents
                and "features_request" not in input_docs
                and self._doc_store.exists("features")
            ):
                input_docs["features_request"] = self._doc_store.read("features")
            # Per-track alias: agents declare ``uat_prereqs``, engine writes it
            # as ``uat_prereqs_<track.name>``. Inject the per-track doc under
            # the generic name the agent expects.
            prereqs_doc = f"uat_prereqs_{track.name}"
            if (
                "uat_prereqs" in agent_def.input_documents
                and "uat_prereqs" not in input_docs
                and self._doc_store.exists(prereqs_doc)
            ):
                input_docs["uat_prereqs"] = self._doc_store.read(prereqs_doc)
            # Track spec is the per-track input.
            if self._doc_store.exists(spec_doc):
                input_docs[spec_doc] = self._doc_store.read(spec_doc)

            extra_context = self._update_extra_context(state)
            extra_context["track_name"] = track.name
            extra_context["track_kind"] = track.kind
            extra_context["frontend_kind"] = track.uat_kind or track.kind
            extra_context["run_id"] = run_id

            result = await run_qa_cycle(
                claude=self._claude,
                action_agent=agent_def,
                qa_agent=self._registry.get("uat_qa"),
                input_docs=input_docs,
                output_doc_name=f"uat_report_{track.name}",
                # uat_qa.md.j2 references ``{{ uat_report }}`` — feed the
                # action agent's output under that canonical key, not the
                # per-track filename.
                qa_output_key="uat_report",
                workspace=self._project_dir / track.path,
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
                session_id=None,
                extra_context=extra_context,
            )
            total_cost += result.total_cost
            raw_report = self._doc_store.read(f"uat_report_{track.name}")
            validated = validate_uat_report(raw_report, uat_mode=cfg.uat_mode)
            if validated != raw_report:
                self._doc_store.write(f"uat_report_{track.name}", validated)
            per_track_reports[track.name] = validated
            self._record_agent_run(state, agent_name, result.total_cost)

        aggregated = aggregate_uat_reports(per_track_reports)
        self._doc_store.write("uat_report", aggregated)

        state.total_cost_usd += total_cost
        state.active_session_id = None
        await self._commit_docs_changes("docs: UAT report")

        return advance_phase(state, PipelinePhase.UAT_QA)

    async def _run_single_agent(
        self,
        agent_name: str,
        input_docs: dict[str, str],
        output_doc_name: str,
        extra_context: dict[str, str] | None = None,
    ) -> str:
        """Run a single agent without a QA cycle."""
        agent_def = self._registry.get(agent_name)
        prompt = self._prompt_renderer.render_agent_prompt(
            template_name=agent_def.prompt_template,
            input_documents=input_docs,
            constraints=agent_def.constraints,
            extra_context=extra_context,
        )
        config = to_run_config(agent_def)
        result = await self._claude.run(
            agent=config,
            prompt=prompt,
            working_dir=self._project_dir,
        )

        if not result.text.strip():
            await asyncio.sleep(5.0)
            result = await self._claude.run(
                agent=config,
                prompt=prompt,
                working_dir=self._project_dir,
            )

        if not result.text.strip():
            raise AgentRunError(
                agent_name=agent_name,
                message="Agent returned empty output",
            )

        return result.text

    def _record_agent_run(
        self,
        state: PipelineState,
        agent_name: str,
        cost: float,
    ) -> None:
        """Add an AgentRunRecord to the pipeline state."""
        state.agent_runs.append(
            AgentRunRecord(
                agent_name=agent_name,
                phase=state.phase,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                cost_usd=cost,
                success=True,
            )
        )

    @staticmethod
    def _validate_sprint_feature_conventions(
        sprints: list[SprintState],
        features_text: str,
    ) -> list[str]:
        """Validate EXISTING-F and DELETED-F conventions in sprint scopes.

        Returns a list of warning strings for any violations found:
        - Sprints containing [EXISTING-F...] features (should not be re-implemented)
        - [DELETED-F...] features in the features doc with no cleanup sprint
        """
        existing_re = re.compile(r"\[EXISTING-F\d+\]")
        deleted_re = re.compile(r"\[DELETED-F(\d+)\]")

        warnings: list[str] = []

        for sprint in sprints:
            matches = existing_re.findall(sprint.scope_text)
            if matches:
                warnings.append(
                    f"Sprint {sprint.sprint_number} ({sprint.name}) "
                    f"contains existing features that should not be "
                    f"re-implemented: {', '.join(matches)}"
                )

        deleted_ids = set(deleted_re.findall(features_text))
        if deleted_ids:
            all_scope_text = " ".join(s.scope_text for s in sprints)
            for fid in sorted(deleted_ids):
                if f"[DELETED-F{fid}]" not in all_scope_text and f"F{fid}" not in all_scope_text:
                    warnings.append(
                        f"Feature [DELETED-F{fid}] has no cleanup sprint scheduled"
                    )

        return warnings

    _SPRINT_HEADER_COUNT_RE = re.compile(r"^##\s+Sprint\s+\d+:", re.MULTILINE)

    async def _parse_sprint_plan(self, plan_text: str) -> list[SprintState]:
        """Extract sprint entries from the plan text via an LLM parser.

        The plan is prose-rich markdown that frequently contains narrative
        references like "dependency on **Sprint 4:**" inside notes sections.
        Regex-only parsing was vulnerable to misreading those as headers and
        emitting phantom sprint entries; an LLM parser handles the prose
        cleanly. A regex-based ``^##\\s+Sprint\\s+\\d+:`` count acts as a
        sanity check on the LLM's output, catching hallucinated extras or
        omissions.

        Raises ``OutputParseError`` when no real Sprint header is present
        (we fail loudly rather than fabricating a default sprint).
        """
        header_count = len(self._SPRINT_HEADER_COUNT_RE.findall(plan_text))
        if header_count == 0:
            raise OutputParseError(
                agent_name="sprint_planner",
                message="No '## Sprint N:' headers found in sprint plan",
            )

        def sanity_check(parsed: ParsedSprintPlan) -> None:
            if len(parsed.sprints) != header_count:
                raise ValueError(
                    f"sprint count mismatch: input has {header_count} "
                    f"'## Sprint N:' headers, LLM returned {len(parsed.sprints)}",
                )
            seen: set[int] = set()
            for entry in parsed.sprints:
                if entry.sprint_number in seen:
                    raise ValueError(
                        f"duplicate sprint_number {entry.sprint_number}",
                    )
                seen.add(entry.sprint_number)

        extraction_prompt = (
            "Extract every sprint defined in the sprint plan below. A sprint "
            "is introduced by a markdown header of the form `## Sprint N: "
            "<name>` (where N is an integer). Narrative paragraphs that "
            "merely reference a sprint by number (e.g. inside a notes section "
            "or a bullet about dependencies) are NOT sprint definitions and "
            "must be ignored.\n\n"
            "For each sprint, extract:\n"
            "- sprint_number: the integer N from the header\n"
            "- name: the text after the colon on the header line\n"
            "- scope_text: the full block from the header up to (but not "
            "including) the next sprint header or end of document, trimmed\n"
            "- needs_integration: true iff a `**Needs Integration:** yes` "
            "line appears inside the sprint block\n"
            "- integration_services: when needs_integration is true, the "
            "comma-separated list from `**Integration Services:**`, "
            "lowercased and trimmed; otherwise an empty list\n"
            "- tracks_in_scope: comma-separated list from `**Tracks in scope:**`, "
            "trimmed; empty list if the field is absent"
        )

        parsed = await parse_with_llm(
            claude=self._claude,
            text=plan_text,
            schema_model=ParsedSprintPlan,
            extraction_prompt=extraction_prompt,
            working_dir=self._project_dir,
            sanity_check=sanity_check,
            agent_name="sprint_plan_parser",
        )

        return [
            SprintState(
                sprint_number=entry.sprint_number,
                name=entry.name,
                scope_text=entry.scope_text,
                integration_services=[
                    s.strip().lower() for s in entry.integration_services if s.strip()
                ],
                tracks_in_scope=[
                    s.strip() for s in entry.tracks_in_scope if s.strip()
                ],
            )
            for entry in parsed.sprints
        ]
