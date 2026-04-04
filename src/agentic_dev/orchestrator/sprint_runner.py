"""Sprint runner: executes a single sprint through backend -> frontend -> integration."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
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
    ) -> None:
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._project_dir = project_dir
        self._has_backend = project_type in ("fullstack", "backend_only")
        self._has_frontend = project_type in ("fullstack", "frontend_only")

    async def run_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
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
                sprint_number, sprint_scope, needs_integration, partial_cost
            )

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
    ) -> SprintResult:
        """Run the backend, frontend, and optional integration QA cycles.

        partial_cost is a single-element list used as a mutable accumulator so
        the caller can recover cost even if this method raises AgentRunError.
        """
        backend_spec = self._doc_store.read("backend_spec") if self._has_backend else ""
        frontend_spec = self._doc_store.read("frontend_spec") if self._has_frontend else ""
        api_contract = self._doc_store.read("api_contract") if self._has_backend else ""

        # Include checkpoint feedback as additional context if available
        extra_context: dict[str, str] = {}
        if self._doc_store.exists("checkpoint_feedback"):
            extra_context["user_feedback"] = self._doc_store.read("checkpoint_feedback")

        # Backend QA cycle
        backend_result = None
        if self._has_backend:
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
                workspace=self._project_dir / "backend",
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
            )
            partial_cost[0] += backend_result.total_cost

        # Frontend QA cycle
        frontend_result = None
        if self._has_frontend:
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
                workspace=self._project_dir / "frontend",
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
            )
            partial_cost[0] += frontend_result.total_cost

        # Optional integration QA cycle
        integration_result = None
        if needs_integration:
            emit(_event_log, SprintPhaseEvent(sprint_number=sprint_number, sub_phase="integration", message=f"Sprint {sprint_number}: integration"))
            integration_input_docs = {
                "backend_spec": backend_spec,
                "frontend_spec": frontend_spec,
                "api_contract": api_contract,
                "sprint_scope": sprint_scope,
                **extra_context,
            }
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
            )
            partial_cost[0] += integration_result.total_cost

        return SprintResult(
            sprint_number=sprint_number,
            success=True,
            total_cost=partial_cost[0],
            backend_result=backend_result,
            frontend_result=frontend_result,
            integration_result=integration_result,
        )
