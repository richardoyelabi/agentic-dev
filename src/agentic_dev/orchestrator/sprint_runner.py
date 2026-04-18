"""Sprint runner: executes a single sprint through backend -> frontend -> integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.mcp.claude_settings import discover_mcp_servers, find_server_for_service
from agentic_dev.documents.scoping import extract_sprint_feature_ids, scope_spec_to_features
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError, RateLimitError
from agentic_dev.onboarding.figma import FigmaMCPNotConfigured, check_figma_mcp_available
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.context import get_run_context
from agentic_dev.logging.events import (
    SprintStartEvent,
    SprintPhaseEvent,
    SprintCompleteEvent,
    SprintFailedEvent,
)
from agentic_dev.orchestrator.qa_cycle import QACycleResult, run_qa_cycle
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelineState, SprintState, SprintStatus

# Maps each SprintStatus to a unique ordinal for fine-grained skip comparison.
# Each sub-step (dev, qa, correction) gets its own position so that resume
# after a crash can skip to the exact point where work was interrupted.
_STEP_ORDER: dict[SprintStatus, int] = {
    SprintStatus.PENDING: 0,
    SprintStatus.BACKEND_DEV: 1,
    SprintStatus.BACKEND_QA: 2,
    SprintStatus.BACKEND_CORRECTION: 3,
    SprintStatus.FRONTEND_DEV: 4,
    SprintStatus.FRONTEND_QA: 5,
    SprintStatus.FRONTEND_CORRECTION: 6,
    SprintStatus.INTEGRATION: 7,
    SprintStatus.INTEGRATION_QA: 8,
    SprintStatus.INTEGRATION_CORRECTION: 9,
    SprintStatus.COMPLETE: 10,
    SprintStatus.FAILED: 0,
}


def _should_skip(current_status: SprintStatus, step: SprintStatus) -> bool:
    """Return True if ``step`` was already completed based on ``current_status``."""
    return _STEP_ORDER[current_status] > _STEP_ORDER[step]

_event_log = get_event_logger("sprint_runner")


@dataclass(frozen=True)
class SprintResult:
    """Outcome of a full sprint execution."""

    sprint_number: int
    success: bool
    total_cost: float
    backend_result: QACycleResult | None = None
    frontend_result: QACycleResult | None = None
    integration_result: QACycleResult | None = None
    error: str | None = None


class SprintRunner:
    """Orchestrates backend, frontend, and optional integration QA cycles for a sprint."""

    def __init__(
        self,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        project_dir: Path,
        project_type: str = "fullstack",
        state_manager: StateManager | None = None,
        pipeline_state: PipelineState | None = None,
        directory_map: DirectoryMap | None = None,
    ) -> None:
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._project_dir = project_dir
        self._directory_map = directory_map or DirectoryMap(
            frontend="frontend", backend="backend",
        )
        self._has_backend = project_type in ("fullstack", "backend_only")
        self._has_frontend = project_type in ("fullstack", "frontend_only")
        self._state_manager = state_manager
        self._pipeline_state = pipeline_state

    _SUMMARY_LINES_PER_SPRINT = 10

    def _save_state(self) -> None:
        """Save pipeline state if state_manager is configured."""
        if self._state_manager is not None and self._pipeline_state is not None:
            self._state_manager.save(self._pipeline_state)

    def _update_rolling_summary(self, sprint_number: int) -> None:
        """Append this sprint's summary to the rolling summary document.

        Keeps only the last ``_SUMMARY_LINES_PER_SPRINT`` lines from each
        sub-phase output, producing a fixed-size entry per sprint.
        """
        parts: list[str] = []
        for suffix in ("backend", "frontend", "integration"):
            doc_name = f"sprint_{sprint_number}_{suffix}"
            if self._doc_store.exists(doc_name):
                content = self._doc_store.read(doc_name)
                lines = content.strip().splitlines()
                tail = lines[-self._SUMMARY_LINES_PER_SPRINT:] if len(lines) > self._SUMMARY_LINES_PER_SPRINT else lines
                parts.append(
                    f"### Sprint {sprint_number} ({suffix})\n" + "\n".join(tail)
                )

        if not parts:
            return

        new_entry = "\n\n".join(parts)
        if self._doc_store.exists("sprint_rolling_summary"):
            existing = self._doc_store.read("sprint_rolling_summary")
            updated = existing.rstrip() + "\n\n" + new_entry + "\n"
        else:
            updated = "## Prior Sprint Summaries\n\n" + new_entry + "\n"

        self._doc_store.write("sprint_rolling_summary", updated)

    def _resolve_integration_mcp_config(self, services: list[str]) -> Path | None:
        """Check MCP availability for integration services.

        Claude Code subprocesses inherit all MCP servers from the user's
        settings, so no ``--mcp-config`` flag is needed. This method only
        logs warnings for services not found in the Claude Code environment.

        Returns None always — the subprocess inherits configured servers.
        """
        if not services:
            return None

        env = discover_mcp_servers(project_dir=self._project_dir)
        for service in services:
            if find_server_for_service(env, service) is None:
                _event_log.warning(
                    "No MCP server for '%s' found in Claude Code settings. "
                    "Run 'claude mcp add %s' to configure it.",
                    service,
                    service,
                )
        return None

    async def run_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        sprint_state: SprintState | None = None,
        needs_integration: bool = False,
    ) -> SprintResult:
        """Run a complete sprint: backend -> frontend -> optional integration.

        Args:
            sprint_number: The 1-based sprint number.
            sprint_scope: Sprint-specific scope extracted from the sprint plan.
            needs_integration: Whether to run the integration QA cycle.

        Returns:
            SprintResult with costs and success status.
        """
        partial_cost: list[float] = [0.0]
        try:
            start_time = datetime.now(timezone.utc)
            emit(_event_log, SprintStartEvent(
                sprint_number=sprint_number,
                sprint_name=sprint_scope,
                needs_integration=needs_integration,
                message=f"Sprint {sprint_number} started: {sprint_scope}",
            ))

            # Set sprint context for child events
            ctx = get_run_context()
            if ctx is not None:
                ctx.sprint_number = sprint_number

            result = await self._execute_sprint(
                sprint_number, sprint_scope, needs_integration, partial_cost,
                sprint_state,
            )

            self._update_rolling_summary(sprint_number)

            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            emit(_event_log, SprintCompleteEvent(
                sprint_number=sprint_number,
                success=True,
                total_cost=result.total_cost,
                duration_s=duration_s,
                message=f"Sprint {sprint_number} complete (${result.total_cost:.4f}, {duration_s:.1f}s)",
            ))

            if ctx is not None:
                ctx.sprint_number = None

            return result
        except RateLimitError:
            # Do NOT convert to a failed SprintResult — the engine
            # handles rate limits via a pause-and-resume path that
            # preserves the sprint state for retry.
            if ctx is not None:
                ctx.sprint_number = None
            raise
        except AgentRunError as exc:
            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            emit(_event_log, SprintFailedEvent(
                sprint_number=sprint_number,
                error=str(exc),
                partial_cost=partial_cost[0],
                level="ERROR",
                message=f"Sprint {sprint_number} failed: {exc}",
            ))

            if ctx is not None:
                ctx.sprint_number = None

            return SprintResult(
                sprint_number=sprint_number,
                success=False,
                total_cost=partial_cost[0],
                error=str(exc),
            )

    async def _execute_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        needs_integration: bool,
        partial_cost: list[float],
        sprint_state: SprintState | None = None,
    ) -> SprintResult:
        """Run the backend, frontend, and optional integration QA cycles.

        partial_cost is a single-element list used as a mutable accumulator so
        the caller can recover cost even if this method raises AgentRunError.

        When sprint_state is provided, sub-step progress is checkpointed to
        disk after each QA cycle completes. On resume, completed sub-steps
        are skipped based on the sprint's current status.
        """
        current_status = sprint_state.status if sprint_state else SprintStatus.PENDING

        def _make_on_substep(qa_status: SprintStatus, correction_status: SprintStatus):
            """Create an on_substep callback that checkpoints sprint sub-steps."""
            def callback(substep: str) -> None:
                if sprint_state is None:
                    return
                status_map = {"qa": qa_status, "correction": correction_status}
                if substep in status_map:
                    sprint_state.status = status_map[substep]
                    self._save_state()
            return callback

        # Scope specs to only sprint-relevant sections based on feature IDs
        feature_ids = extract_sprint_feature_ids(sprint_scope)
        backend_spec = (
            scope_spec_to_features(self._doc_store.read("backend_spec"), feature_ids)
            if self._has_backend else ""
        )
        frontend_spec = (
            scope_spec_to_features(self._doc_store.read("frontend_spec"), feature_ids)
            if self._has_frontend else ""
        )
        api_contract = (
            scope_spec_to_features(self._doc_store.read("api_contract"), feature_ids)
            if self._has_backend else ""
        )

        extra_context: dict[str, str] = {}

        # Use rolling summary for cross-sprint context (constant size)
        if self._doc_store.exists("sprint_rolling_summary"):
            extra_context["prior_sprint_summaries"] = (
                self._doc_store.read("sprint_rolling_summary")
            )

        if self._doc_store.exists("checkpoint_feedback"):
            extra_context["user_feedback"] = self._doc_store.read("checkpoint_feedback")
        if (
            self._pipeline_state is not None
            and self._pipeline_state.mode == "update"
            and self._doc_store.exists("user_input")
        ):
            extra_context["change_request"] = self._doc_store.read("user_input")

        if self._doc_store.exists("spec_changes"):
            extra_context["spec_changes"] = self._doc_store.read("spec_changes")
        if self._doc_store.exists("design_changes"):
            extra_context["design_changes"] = self._doc_store.read("design_changes")

        if self._doc_store.exists("figma_sources"):
            extra_context["figma_sources"] = self._doc_store.read("figma_sources")
            try:
                check_figma_mcp_available()
                extra_context["figma_mcp_available"] = "true"
            except FigmaMCPNotConfigured:
                _event_log.warning(
                    "Figma MCP server not configured. Frontend agents will "
                    "fall back to text-based design references."
                )
                extra_context["figma_mcp_available"] = "false"

        if (
            self._pipeline_state is not None
            and self._pipeline_state.frontend_kind is not None
        ):
            extra_context["frontend_kind"] = self._pipeline_state.frontend_kind.value

        # Backend QA cycle
        backend_result = None
        if self._has_backend and not _should_skip(current_status, SprintStatus.BACKEND_CORRECTION):
            if sprint_state is not None:
                sprint_state.status = SprintStatus.BACKEND_DEV
                self._save_state()

            emit(_event_log, SprintPhaseEvent(sprint_number=sprint_number, sub_phase="backend_dev", message=f"Sprint {sprint_number}: backend development"))
            backend_input_docs = {
                "backend_spec": backend_spec,
                "api_contract": api_contract,
                "sprint_scope": sprint_scope,
                **extra_context,
            }
            backend_result = await run_qa_cycle(
                claude=self._claude,
                action_agent=self._registry.get("backend_developer"),
                qa_agent=self._registry.get("backend_qa"),
                input_docs=backend_input_docs,
                output_doc_name=f"sprint_{sprint_number}_backend",
                workspace=self._project_dir / (self._directory_map.backend or "backend"),
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
                session_id=sprint_state.backend_session_id if sprint_state else None,
                on_substep=_make_on_substep(SprintStatus.BACKEND_QA, SprintStatus.BACKEND_CORRECTION),
                skip_to_correction=_should_skip(current_status, SprintStatus.BACKEND_QA),
                skip_action_output_in_qa=True,
            )
            partial_cost[0] += backend_result.total_cost

            if sprint_state is not None:
                sprint_state.backend_session_id = backend_result.session_id
                sprint_state.status = SprintStatus.FRONTEND_DEV if self._has_frontend else (
                    SprintStatus.INTEGRATION if needs_integration else SprintStatus.COMPLETE
                )
                self._save_state()
                current_status = sprint_state.status

        # Frontend QA cycle
        frontend_result = None
        if self._has_frontend and not _should_skip(current_status, SprintStatus.FRONTEND_CORRECTION):
            if sprint_state is not None:
                sprint_state.status = SprintStatus.FRONTEND_DEV
                self._save_state()

            emit(_event_log, SprintPhaseEvent(sprint_number=sprint_number, sub_phase="frontend_dev", message=f"Sprint {sprint_number}: frontend development"))
            frontend_input_docs = {
                "frontend_spec": frontend_spec,
                "api_contract": api_contract,
                "sprint_scope": sprint_scope,
                **extra_context,
            }
            frontend_result = await run_qa_cycle(
                claude=self._claude,
                action_agent=self._registry.get("frontend_developer"),
                qa_agent=self._registry.get("frontend_qa"),
                input_docs=frontend_input_docs,
                output_doc_name=f"sprint_{sprint_number}_frontend",
                workspace=self._project_dir / (self._directory_map.frontend or "frontend"),
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
                session_id=sprint_state.frontend_session_id if sprint_state else None,
                on_substep=_make_on_substep(SprintStatus.FRONTEND_QA, SprintStatus.FRONTEND_CORRECTION),
                skip_to_correction=_should_skip(current_status, SprintStatus.FRONTEND_QA),
                skip_action_output_in_qa=True,
            )
            partial_cost[0] += frontend_result.total_cost

            if sprint_state is not None:
                sprint_state.frontend_session_id = frontend_result.session_id
                sprint_state.status = SprintStatus.INTEGRATION if needs_integration else SprintStatus.COMPLETE
                self._save_state()
                current_status = sprint_state.status

        # Optional integration QA cycle
        integration_result = None
        if needs_integration and not _should_skip(current_status, SprintStatus.INTEGRATION_CORRECTION):
            if sprint_state is not None:
                sprint_state.status = SprintStatus.INTEGRATION
                self._save_state()

            emit(_event_log, SprintPhaseEvent(sprint_number=sprint_number, sub_phase="integration", message=f"Sprint {sprint_number}: integration"))
            integration_input_docs = {
                "backend_spec": backend_spec,
                "frontend_spec": frontend_spec,
                "api_contract": api_contract,
                "sprint_scope": sprint_scope,
                **extra_context,
            }
            integration_services = sprint_state.integration_services if sprint_state else []
            mcp_config = self._resolve_integration_mcp_config(integration_services)
            # Note: skip_action_output_in_qa is NOT set here (unlike backend/
            # frontend QA). Integration QA reviews the Integration Guide text
            # itself via ``{{ integration_guide }}`` in its template, so the
            # action output must be passed through to the QA prompt.
            integration_result = await run_qa_cycle(
                claude=self._claude,
                action_agent=self._registry.get("integration"),
                qa_agent=self._registry.get("integration_qa"),
                input_docs=integration_input_docs,
                output_doc_name=f"sprint_{sprint_number}_integration",
                workspace=self._project_dir,
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
                qa_output_key="integration_guide",
                session_id=sprint_state.integration_session_id if sprint_state else None,
                on_substep=_make_on_substep(SprintStatus.INTEGRATION_QA, SprintStatus.INTEGRATION_CORRECTION),
                skip_to_correction=_should_skip(current_status, SprintStatus.INTEGRATION_QA),
                mcp_config=mcp_config,
            )
            partial_cost[0] += integration_result.total_cost

            if sprint_state is not None:
                sprint_state.integration_session_id = integration_result.session_id
                sprint_state.status = SprintStatus.COMPLETE
                self._save_state()

        return SprintResult(
            sprint_number=sprint_number,
            success=True,
            total_cost=partial_cost[0],
            backend_result=backend_result,
            frontend_result=frontend_result,
            integration_result=integration_result,
        )
